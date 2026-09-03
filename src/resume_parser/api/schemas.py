"""Request and response bodies specific to the HTTP layer.

Domain models are reused directly wherever they fit; these types exist only where the wire
contract genuinely differs from the domain - batch envelopes, match requests that accept
either structured requirements or raw job text, and health payloads.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from resume_parser.domain.matching import JobRequirements, MatchResult
from resume_parser.domain.results import ParseResult

__all__ = [
    "BatchItemResult",
    "BatchParseResponse",
    "HealthResponse",
    "MatchRequest",
    "MatchResponse",
    "ReadinessResponse",
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BatchItemResult(_Base):
    """One entry in a batch response - either a result or a structured error."""

    filename: str | None = Field(default=None, description="Original filename.")
    status: Literal["success", "error"] = Field(description="Outcome for this document.")
    result: ParseResult | None = Field(default=None, description="Present when successful.")
    error_code: str | None = Field(default=None, description="Machine-readable failure slug.")
    error_detail: str | None = Field(default=None, description="Human-readable failure reason.")


class BatchParseResponse(_Base):
    """The result of a batch parse request."""

    total: int = Field(description="Documents submitted.")
    succeeded: int = Field(description="Documents parsed successfully.")
    failed: int = Field(description="Documents that failed.")
    results: list[BatchItemResult] = Field(description="Per-document outcomes, in request order.")


class MatchRequest(_Base):
    """Score an already-parsed resume against a job.

    Accepts either structured ``requirements`` or free-text ``job_description``. Supplying
    the structured form skips the LLM call entirely, which makes scoring free and instant -
    worth doing when the same posting is scored against many candidates.
    """

    resume: ParseResult = Field(description="A previous /v1/parse response, passed back verbatim.")
    requirements: JobRequirements | None = Field(
        default=None, description="Structured requirements. Takes precedence over job text."
    )
    job_description: str | None = Field(
        default=None,
        max_length=100_000,
        description="Raw job posting text; structured with one LLM call when supplied.",
    )

    @model_validator(mode="after")
    def _require_a_job(self) -> MatchRequest:
        if self.requirements is None and not (self.job_description or "").strip():
            msg = "Provide either 'requirements' or 'job_description'."
            raise ValueError(msg)
        return self


class MatchResponse(_Base):
    """A match score plus the requirements it was scored against."""

    match: MatchResult = Field(description="Score, breakdown, matched skills and gaps.")
    requirements: JobRequirements = Field(description="Requirements used for scoring.")
    parse: ParseResult | None = Field(
        default=None, description="Present when the resume was parsed as part of this call."
    )


class HealthResponse(_Base):
    """Liveness payload."""

    status: Literal["ok"] = "ok"
    version: str = Field(description="Package version.")
    environment: str = Field(description="Configured deployment environment.")


class ReadinessResponse(_Base):
    """Readiness payload: whether the service can actually serve a parse."""

    status: Literal["ready", "degraded"] = Field(description="Overall readiness.")
    version: str = Field(description="Package version.")
    providers_configured: list[str] = Field(description="Models with usable credentials.")
    models_unavailable: list[str] = Field(description="Configured models missing credentials.")
    cache: dict[str, float | int] = Field(description="Cache hit/miss counters.")
    supported_formats: list[str] = Field(description="Accepted document formats.")
