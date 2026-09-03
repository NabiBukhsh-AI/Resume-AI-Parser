"""The resilient LLM client: retries, model fallback, and a JSON repair pass.

Three layers of resilience, each handling a different failure mode:

1. **Retry** the same model on transient faults (429, 5xx, timeout) with exponential
   backoff and jitter. Jitter matters under concurrency - without it, a batch of parallel
   parses retries in lockstep and re-creates the burst that caused the rate limit.
2. **Fall through** to the next model in the chain on a non-transient failure, so a bad
   deploy or a deprecated model id degrades service instead of stopping it.
3. **Repair** malformed JSON with a single follow-up call before giving up.

Deterministic, non-retryable errors (a mis-configured provider, a schema the API rejects)
short-circuit immediately: retrying them just multiplies latency.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from resume_parser.domain.results import TokenUsage, UsageMetadata
from resume_parser.exceptions import (
    AllProvidersFailedError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    ProviderNotConfiguredError,
    StructuredOutputError,
)
from resume_parser.llm.anthropic_provider import AnthropicProvider
from resume_parser.llm.base import LLMProvider, StructuredRequest, StructuredResponse
from resume_parser.llm.openai_compatible import OpenAICompatibleProvider, OpenRouterProvider
from resume_parser.llm.prompts import PROMPT_VERSION, build_repair_prompt
from resume_parser.observability.logging import get_logger
from resume_parser.settings import ModelSpec, Settings

__all__ = ["LLMClient", "build_providers"]

logger = get_logger(__name__)

#: Failures worth trying the same model again for.
_RETRYABLE = (LLMRateLimitError, LLMTimeoutError)


def build_providers(settings: Settings) -> dict[str, LLMProvider]:
    """Instantiate one provider per backend named in the settings.

    Providers are constructed for every configured backend, credentialed or not: the
    credential check happens on first use, which keeps startup independent of which keys
    happen to be present and gives a clear error at call time instead of at import.
    """
    timeout = settings.llm.timeout_seconds
    providers: dict[str, LLMProvider] = {
        "anthropic": AnthropicProvider(
            settings.secret_for("anthropic"),
            timeout=timeout,
        ),
        "openrouter": OpenRouterProvider(
            settings.secret_for("openrouter"),
            timeout=timeout,
        ),
        "openai": OpenAICompatibleProvider(
            settings.secret_for("openai"),
            base_url=settings.openai_base_url,
            timeout=timeout,
        ),
    }
    return providers


class LLMClient:
    """Executes a :class:`StructuredRequest` against a chain of models."""

    def __init__(
        self,
        settings: Settings,
        providers: dict[str, LLMProvider] | None = None,
    ) -> None:
        self._settings = settings
        self._providers = providers if providers is not None else build_providers(settings)

    @property
    def chain(self) -> list[ModelSpec]:
        """The fallback chain, filtered to models whose provider has credentials."""
        usable = self._settings.configured_models()
        if not usable:
            configured = ", ".join(spec.label for spec in self._settings.llm.models) or "none"
            raise ProviderNotConfiguredError(
                "No LLM provider is configured. Set ANTHROPIC_API_KEY (or OPENROUTER_API_KEY "
                f"/ OPENAI_API_KEY) for one of the models in the chain: {configured}.",
            )
        return usable

    async def generate(self, request: StructuredRequest) -> tuple[dict[str, Any], UsageMetadata]:
        """Run ``request`` through the chain and return the payload plus usage metadata.

        Raises:
            AllProvidersFailedError: Every model in the chain failed.
            ProviderNotConfiguredError: No model in the chain has credentials.
        """
        chain = self.chain
        started = time.perf_counter()
        attempts = 0
        failures: list[str] = []

        for index, spec in enumerate(chain):
            provider = self._providers.get(spec.provider)
            if provider is None:
                failures.append(f"{spec.label}: no such provider")
                continue

            try:
                response, model_attempts = await self._call_with_retries(provider, request, spec)
            except ProviderNotConfiguredError as exc:
                attempts += 1
                failures.append(f"{spec.label}: {exc.message}")
                logger.warning("model_unavailable", model=spec.label, reason=exc.message)
                continue
            except LLMError as exc:
                attempts += 1
                failures.append(f"{spec.label}: {exc.message}")
                logger.warning(
                    "model_failed",
                    model=spec.label,
                    reason=exc.message,
                    remaining=len(chain) - index - 1,
                )
                continue

            attempts += model_attempts
            usage = self._build_usage(
                spec=spec,
                response=response,
                attempts=attempts,
                latency_ms=int((time.perf_counter() - started) * 1000),
                fallback_used=index > 0,
            )
            return response.data, usage

        raise AllProvidersFailedError(
            "Every configured model failed to produce a usable result.",
            failures=failures,
            attempts=attempts,
        )

    async def _call_with_retries(
        self,
        provider: LLMProvider,
        request: StructuredRequest,
        spec: ModelSpec,
    ) -> tuple[StructuredResponse, int]:
        """Call one model, retrying transient failures and repairing malformed JSON."""
        max_attempts = self._settings.llm.max_retries + 1
        last_error: LLMError | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                return await provider.generate(request, spec), attempt
            except StructuredOutputError as exc:
                last_error = exc
                repaired = await self._try_repair(provider, request, spec, exc)
                if repaired is not None:
                    return repaired, attempt + 1
                # A schema violation will not fix itself on a plain retry.
                raise
            except _RETRYABLE as exc:
                last_error = exc
                if attempt == max_attempts:
                    break
                delay = self._backoff_delay(attempt)
                logger.info(
                    "llm_retry",
                    model=spec.label,
                    attempt=attempt,
                    delay_seconds=round(delay, 2),
                    reason=type(exc).__name__,
                )
                await asyncio.sleep(delay)

        raise last_error or LLMError(f"{spec.label} failed without reporting an error.")

    async def _try_repair(
        self,
        provider: LLMProvider,
        request: StructuredRequest,
        spec: ModelSpec,
        error: StructuredOutputError,
    ) -> StructuredResponse | None:
        """One follow-up call asking the model to fix its own output. ``None`` if disabled."""
        if not self._settings.llm.enable_repair_pass:
            return None
        raw = getattr(error, "context", {}).get("raw_text", "")
        if not raw:
            return None

        logger.info("llm_repair_pass", model=spec.label, reason=error.message)
        repair_request = StructuredRequest(
            system=request.system,
            user=build_repair_prompt(raw, error.message),
            schema=request.schema,
            schema_name=request.schema_name,
            max_output_tokens=request.max_output_tokens,
            effort="low",  # Reformatting is mechanical; deep reasoning adds only cost.
            metadata={**request.metadata, "pass": "repair"},
        )
        try:
            return await provider.generate(repair_request, spec)
        except LLMError as exc:
            logger.warning("llm_repair_failed", model=spec.label, reason=exc.message)
            return None

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with full jitter, capped at 30 seconds."""
        base = self._settings.llm.retry_base_delay * (2 ** (attempt - 1))
        return random.uniform(0, min(base, 30.0))  # noqa: S311 - jitter, not cryptography

    def _build_usage(
        self,
        *,
        spec: ModelSpec,
        response: StructuredResponse,
        attempts: int,
        latency_ms: int,
        fallback_used: bool,
    ) -> UsageMetadata:
        """Attach cost, timing and provenance to a successful call."""
        return UsageMetadata(
            provider=spec.provider,
            model=response.model or spec.model,
            tokens=response.usage,
            estimated_cost_usd=estimate_cost(response.usage, spec),
            latency_ms=latency_ms,
            attempts=attempts,
            cached=False,
            fallback_used=fallback_used,
            prompt_version=PROMPT_VERSION,
        )

    async def aclose(self) -> None:
        """Close every provider's transport."""
        for provider in self._providers.values():
            await provider.aclose()


def estimate_cost(usage: TokenUsage, spec: ModelSpec) -> float | None:
    """Estimate USD cost from token counts and the model's configured prices.

    Returns ``None`` when the model has no prices configured, which is honest: a wrong
    number in a cost dashboard is worse than a missing one. Cache reads are billed at
    roughly a tenth of the input rate and cache writes at ~1.25x, which is close enough
    for budgeting.
    """
    if spec.input_cost_per_mtok is None or spec.output_cost_per_mtok is None:
        return None
    per_million = 1_000_000.0
    cost = (
        usage.input_tokens * spec.input_cost_per_mtok
        + usage.cache_write_tokens * spec.input_cost_per_mtok * 1.25
        + usage.cache_read_tokens * spec.input_cost_per_mtok * 0.1
        + usage.output_tokens * spec.output_cost_per_mtok
    ) / per_million
    return round(cost, 6)
