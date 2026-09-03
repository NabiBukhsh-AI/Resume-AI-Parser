"""The orchestrator: bytes in, :class:`ParseResult` out.

This is the seam every entry point sits behind. The FastAPI routes, the CLI and the
Streamlit UI all call :class:`ResumeParsingService` and none of them contain business
logic - which is how the same behaviour is guaranteed across all three, and why the whole
pipeline is testable without an HTTP client.

Pipeline order:

    bytes -> extract text -> cache lookup -> LLM structured extraction
          -> pydantic validation -> normalization -> deterministic enrichment -> cache store
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from resume_parser.domain.matching import JobRequirements, MatchResult
from resume_parser.domain.results import ParseResult, UsageMetadata
from resume_parser.domain.resume import Resume, ResumeExtraction
from resume_parser.exceptions import ResumeParserError, StructuredOutputError
from resume_parser.extraction.service import ExtractionService
from resume_parser.llm.base import StructuredRequest
from resume_parser.llm.client import LLMClient
from resume_parser.llm.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    JOB_REQUIREMENTS_SYSTEM_PROMPT,
    PROMPT_VERSION,
    build_extraction_user_prompt,
    build_job_requirements_prompt,
)
from resume_parser.llm.schema import schema_fingerprint, to_strict_json_schema
from resume_parser.observability.logging import get_logger
from resume_parser.pipeline.cache import ParseCache, build_cache_key
from resume_parser.pipeline.enrichment import enrich
from resume_parser.pipeline.matching import match_resume_to_job
from resume_parser.pipeline.normalization import normalize_resume
from resume_parser.settings import Settings

__all__ = ["BatchItem", "ResumeParsingService"]

logger = get_logger(__name__)


@dataclass(slots=True)
class BatchItem:
    """One document in a batch request."""

    data: bytes
    filename: str | None = None


class ResumeParsingService:
    """Coordinates extraction, inference, validation and enrichment."""

    def __init__(
        self,
        settings: Settings,
        *,
        extraction: ExtractionService | None = None,
        llm: LLMClient | None = None,
        cache: ParseCache | None = None,
    ) -> None:
        self._settings = settings
        self._extraction = extraction or ExtractionService(settings.extraction)
        self._llm = llm or LLMClient(settings)
        self._cache = cache or ParseCache(settings.cache)

        # Both schemas are derived once: they are pure functions of the models, and
        # rebuilding them per request would add latency to every call for no benefit.
        self._resume_schema = to_strict_json_schema(ResumeExtraction)
        self._resume_schema_id = schema_fingerprint(self._resume_schema)
        self._job_schema = to_strict_json_schema(JobRequirements)

    @property
    def cache(self) -> ParseCache:
        """The result cache, exposed for health reporting and tests."""
        return self._cache

    @property
    def resume_schema(self) -> dict[str, object]:
        """The strict JSON Schema sent to the model, served by the ``/v1/schema`` route."""
        return self._resume_schema

    # ------------------------------------------------------------------ parsing

    async def parse(self, data: bytes, *, filename: str | None = None) -> ParseResult:
        """Parse one resume document end to end.

        Raises:
            ResumeParserError: Any failure in extraction, inference or validation. The
                subclass carries the HTTP status the API should use.
        """
        started = time.perf_counter()
        document = self._extraction.extract(data, filename=filename)
        warnings = list(document.warnings)

        text, truncated = self._fit_to_budget(document.text)
        if truncated:
            document.info.truncated = True
            warnings.append(
                "Resume text exceeded the configured input budget and was truncated; "
                "later sections may be missing from the result."
            )

        chain = self._llm.chain
        cache_key = build_cache_key(
            content_sha256=document.info.content_sha256,
            model_label=chain[0].label,
            prompt_version=PROMPT_VERSION,
            schema_fingerprint=self._resume_schema_id,
            input_character_limit=self._settings.llm.max_input_characters,
        )

        if cached := await self._cache.get(cache_key):
            logger.info("parse_cache_hit", filename=document.info.filename, key=cache_key[:12])
            result = ParseResult.model_validate(cached)
            result.usage = result.usage.model_copy(
                update={
                    "cached": True,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                }
            )
            result.document = document.info
            return result

        payload, usage = await self._llm.generate(
            StructuredRequest(
                system=EXTRACTION_SYSTEM_PROMPT,
                user=build_extraction_user_prompt(text, filename=document.info.filename),
                schema=self._resume_schema,
                schema_name="resume_extraction",
                max_output_tokens=self._settings.llm.max_output_tokens,
                effort=self._settings.llm.effort,
                metadata={"task": "resume_extraction"},
            )
        )

        resume = self._validate_and_enrich(payload, usage)
        result = ParseResult(
            resume=resume,
            document=document.info,
            usage=usage,
            warnings=warnings + self._quality_warnings(resume),
        )

        await self._cache.set(cache_key, result.model_dump(mode="json"))
        logger.info(
            "parse_complete",
            filename=document.info.filename,
            model=usage.model,
            latency_ms=usage.latency_ms,
            tokens=usage.tokens.total_tokens,
            cost_usd=usage.estimated_cost_usd,
            completeness=resume.analytics.completeness_score,
        )
        return result

    async def parse_batch(self, items: Sequence[BatchItem]) -> list[ParseResult | Exception]:
        """Parse many documents with bounded concurrency.

        Failures are returned in place rather than raised, so one malformed document in a
        batch of fifty does not discard the other forty-nine. Callers inspect each entry.
        """
        semaphore = asyncio.Semaphore(self._settings.server.batch_concurrency)

        async def run(item: BatchItem) -> ParseResult | Exception:
            async with semaphore:
                try:
                    return await self.parse(item.data, filename=item.filename)
                except ResumeParserError as exc:
                    logger.warning("batch_item_failed", filename=item.filename, reason=exc.message)
                    return exc

        return list(await asyncio.gather(*(run(item) for item in items)))

    # ----------------------------------------------------------------- matching

    async def extract_job_requirements(self, job_text: str) -> JobRequirements:
        """Structure a free-text job description into :class:`JobRequirements`."""
        text, _ = self._fit_to_budget(job_text)
        payload, _ = await self._llm.generate(
            StructuredRequest(
                system=JOB_REQUIREMENTS_SYSTEM_PROMPT,
                user=build_job_requirements_prompt(text),
                schema=self._job_schema,
                schema_name="job_requirements",
                max_output_tokens=4_000,
                effort="low",  # A short, well-specified extraction.
                metadata={"task": "job_requirements"},
            )
        )
        try:
            return JobRequirements.model_validate(payload)
        except ValidationError as exc:
            raise StructuredOutputError(
                f"Job requirements did not match the schema: {exc.error_count()} error(s).",
                errors=exc.errors(include_url=False)[:5],
            ) from exc

    def match(self, resume: Resume, job: JobRequirements) -> MatchResult:
        """Score a parsed resume against structured requirements. Pure and synchronous."""
        return match_resume_to_job(resume, job)

    # ------------------------------------------------------------------ helpers

    def _fit_to_budget(self, text: str) -> tuple[str, bool]:
        """Trim ``text`` to the configured character budget.

        The head of a resume carries identity, summary and the most recent roles - the
        highest-value content - so truncation takes from the tail.
        """
        limit = self._settings.llm.max_input_characters
        if len(text) <= limit:
            return text, False
        return text[:limit], True

    def _validate_and_enrich(self, payload: dict[str, object], usage: UsageMetadata) -> Resume:
        """Validate the model's payload, then normalize and enrich it."""
        try:
            extraction = ResumeExtraction.model_validate(payload)
        except ValidationError as exc:
            raise StructuredOutputError(
                f"Model output failed validation with {exc.error_count()} error(s).",
                model=usage.model,
                errors=exc.errors(include_url=False)[:5],
            ) from exc
        return enrich(normalize_resume(extraction))

    def _quality_warnings(self, resume: Resume) -> list[str]:
        """Surface low-confidence outcomes instead of returning a confident empty record.

        A resume that parses to almost nothing is usually a signal about the input - an
        unusual layout, a cover letter, the wrong file - and the caller should hear about
        it rather than discovering it downstream.
        """
        warnings: list[str] = []
        analytics = resume.analytics
        if analytics.completeness_score < 0.5:
            warnings.append(
                f"Low extraction completeness ({analytics.completeness_score:.0%}). "
                f"Missing: {', '.join(analytics.missing_sections)}."
            )
        if not resume.experience:
            warnings.append("No work experience was found; this may not be a resume.")
        if not resume.contact.email and not resume.contact.phone:
            warnings.append("No contact details were found in the document.")
        return warnings

    async def aclose(self) -> None:
        """Release provider transports held by the service."""
        await self._llm.aclose()
