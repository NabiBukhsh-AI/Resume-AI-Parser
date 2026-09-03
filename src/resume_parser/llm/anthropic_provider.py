"""Anthropic provider, built on the official ``anthropic`` async SDK.

This is the default backend. Extraction is handed to the model as a constrained-decoding
problem via ``output_config.format``, so the response is guaranteed-shaped JSON rather
than prose we have to fish a brace out of.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from resume_parser.domain.results import TokenUsage
from resume_parser.exceptions import (
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    ProviderNotConfiguredError,
    StructuredOutputError,
)
from resume_parser.llm.base import StructuredRequest, StructuredResponse
from resume_parser.observability.logging import get_logger
from resume_parser.settings import ModelSpec

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

__all__ = ["AnthropicProvider"]

logger = get_logger(__name__)

#: Beta flag enabling server-side routing around safety refusals.
_REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    """Calls the Anthropic Messages API with a strict JSON Schema."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str | None,
        *,
        timeout: float = 120.0,
        enable_refusal_fallback: bool = True,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._enable_refusal_fallback = enable_refusal_fallback
        self._client: AsyncAnthropic | None = None

    def _get_client(self) -> AsyncAnthropic:
        """Lazily construct the SDK client so importing this module needs no credentials."""
        if self._client is not None:
            return self._client
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - declared as a hard dependency
            raise ProviderNotConfiguredError(
                "The 'anthropic' package is required for the anthropic provider."
            ) from exc
        if not self._api_key:
            raise ProviderNotConfiguredError(
                "ANTHROPIC_API_KEY is not set; cannot use the anthropic provider."
            )
        # max_retries=0: retry policy lives in the orchestrator so that a retry can also
        # decide to fall through to the next model rather than hammering a dead one.
        self._client = AsyncAnthropic(api_key=self._api_key, timeout=self._timeout, max_retries=0)
        return self._client

    async def generate(self, request: StructuredRequest, spec: ModelSpec) -> StructuredResponse:
        """Request schema-constrained JSON from the configured Claude model."""
        import anthropic

        client = self._get_client()
        params: dict[str, Any] = {
            "model": spec.model,
            "max_tokens": request.max_output_tokens,
            "system": [
                {
                    "type": "text",
                    "text": request.system,
                    # The system prompt and schema are byte-identical across every parse,
                    # so caching the prefix turns most of the input into cache reads.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": request.user}],
            "output_config": {
                "effort": request.effort,
                "format": {
                    "type": "json_schema",
                    "schema": request.schema,
                },
            },
            # Extraction over a long, messy document benefits from letting the model
            # reason before committing to a structure.
            "thinking": {"type": "adaptive"},
        }

        try:
            message = await self._create(client, params)
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError(
                f"Anthropic request timed out after {self._timeout}s.", model=spec.model
            ) from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError("Anthropic rate limit reached.", model=spec.model) from exc
        except anthropic.AuthenticationError as exc:
            raise ProviderNotConfiguredError("Anthropic rejected the API key.") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMError(
                    f"Anthropic server error ({exc.status_code}).", model=spec.model
                ) from exc
            raise LLMError(f"Anthropic rejected the request: {exc}", model=spec.model) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API.", model=spec.model) from exc

        return self._to_response(message, spec)

    async def _create(self, client: AsyncAnthropic, params: dict[str, Any]) -> Any:
        """Issue the call, opting into server-side refusal fallbacks when enabled.

        If the account or model has not been granted the beta, the API answers 400. Rather
        than making every request fail, we drop the flag once and continue without it.
        """
        import anthropic

        if not self._enable_refusal_fallback:
            return await client.messages.create(**params)

        try:
            return await client.beta.messages.create(
                **params, betas=[_REFUSAL_FALLBACK_BETA], fallbacks="default"
            )
        except (anthropic.BadRequestError, TypeError) as exc:
            logger.warning(
                "refusal_fallback_unavailable",
                detail=str(exc),
                hint="Retrying without server-side fallbacks.",
            )
            self._enable_refusal_fallback = False
            return await client.messages.create(**params)

    def _to_response(self, message: Any, spec: ModelSpec) -> StructuredResponse:
        """Convert an SDK message into a :class:`StructuredResponse`."""
        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise LLMError(
                "The model declined to process this document.",
                model=spec.model,
                category=getattr(details, "category", None),
            )
        if stop_reason == "max_tokens":
            raise StructuredOutputError(
                "The response hit the output-token ceiling and is truncated. "
                "Raise llm.max_output_tokens.",
                model=spec.model,
            )

        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise StructuredOutputError("Anthropic returned an empty response.", model=spec.model)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            # raw_text lets the orchestrator run a repair pass instead of failing outright.
            raise StructuredOutputError(
                f"Anthropic returned text that is not valid JSON: {exc}",
                model=spec.model,
                raw_text=text,
            ) from exc
        if not isinstance(data, dict):
            raise StructuredOutputError(
                "Expected a JSON object at the top level.", model=spec.model, raw_text=text
            )

        raw_usage = getattr(message, "usage", None)
        usage = TokenUsage(
            input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
            output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
        )
        return StructuredResponse(
            data=data,
            model=getattr(message, "model", spec.model),
            usage=usage,
            raw_text=text,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP transport."""
        if self._client is not None:
            await self._client.close()
            self._client = None
