"""Providers speaking the OpenAI chat-completions dialect.

One implementation covers OpenRouter, OpenAI itself, and every local runtime that mimics
the same wire format (Ollama, vLLM, LM Studio, LiteLLM). Keeping OpenRouter available
matters for this project's original use case - it is how you run the parser against free
or very cheap models.

Two bugs from the original OpenRouter integration are fixed here:

* ``response_format`` was sent as ``{"type": "json_schema", "schema": ...}``. The API
  expects the schema nested under a ``json_schema`` key with a ``name``, so the constraint
  was ignored and every response came back unconstrained.
* the request had no timeout, so a stalled connection hung the worker indefinitely.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from resume_parser.domain.results import TokenUsage
from resume_parser.exceptions import (
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    ProviderNotConfiguredError,
    StructuredOutputError,
)
from resume_parser.llm.base import StructuredRequest, StructuredResponse
from resume_parser.settings import ModelSpec

__all__ = ["OpenAICompatibleProvider", "OpenRouterProvider", "extract_json_object"]

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """Best-effort recovery of a JSON object from a model's text response.

    Used only for providers that do not honour strict schemas. The order matters: try the
    whole string first, then a fenced code block, and only then fall back to brace
    matching - which, unlike the original ``find("{")`` / ``rfind("}")`` approach, tracks
    string literals so a brace inside a job description does not truncate the payload.
    """
    candidates: list[str] = [text.strip()]
    if fenced := _FENCE_RE.search(text):
        candidates.append(fenced.group(1).strip())
    if balanced := _first_balanced_object(text):
        candidates.append(balanced)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise StructuredOutputError("Response did not contain a JSON object.")


def _first_balanced_object(text: str) -> str | None:
    """Return the first brace-balanced object in ``text``, respecting string literals."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


class OpenAICompatibleProvider:
    """Chat-completions client with strict JSON-Schema response formatting."""

    name = "openai"
    default_base_url = "https://api.openai.com/v1"

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str | None = None,
        timeout: float = 120.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = (base_url or self.default_base_url).rstrip("/")
        self._timeout = timeout
        self._extra_headers = extra_headers or {}
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Lazily build a pooled HTTP client."""
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise ProviderNotConfiguredError(
                f"No API key configured for the '{self.name}' provider."
            )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout, connect=10.0),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                **self._extra_headers,
            },
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
        return self._client

    def build_payload(self, request: StructuredRequest, spec: ModelSpec) -> dict[str, Any]:
        """Assemble the request body. Subclasses extend this with vendor-specific keys."""
        return {
            "model": spec.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": request.max_output_tokens,
            # Extraction is a determinism-seeking task: there is one correct answer in the
            # document, and sampling variance only produces inconsistent results.
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": request.schema,
                },
            },
        }

    async def generate(self, request: StructuredRequest, spec: ModelSpec) -> StructuredResponse:
        """POST to ``/chat/completions`` and decode the structured payload."""
        client = self._get_client()
        try:
            response = await client.post(
                "/chat/completions", json=self.build_payload(request, spec)
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"{self.name} request timed out after {self._timeout}s.", model=spec.model
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach {self.name}: {exc}", model=spec.model) from exc

        self._raise_for_status(response, spec)

        try:
            body = response.json()
        except ValueError as exc:
            raise StructuredOutputError(
                f"{self.name} returned a non-JSON body.", model=spec.model
            ) from exc

        return self._to_response(body, spec)

    def _raise_for_status(self, response: httpx.Response, spec: ModelSpec) -> None:
        """Translate HTTP failures into the domain error hierarchy."""
        if response.is_success:
            return
        detail = response.text[:500]
        if response.status_code in (401, 403):
            raise ProviderNotConfiguredError(
                f"{self.name} rejected the API key.", status=response.status_code
            )
        if response.status_code == 429:
            raise LLMRateLimitError(
                f"{self.name} rate limit reached.", model=spec.model, detail=detail
            )
        if response.status_code >= 500:
            raise LLMError(
                f"{self.name} server error ({response.status_code}).",
                model=spec.model,
                detail=detail,
            )
        raise LLMError(
            f"{self.name} rejected the request ({response.status_code}): {detail}",
            model=spec.model,
        )

    def _to_response(self, body: dict[str, Any], spec: ModelSpec) -> StructuredResponse:
        """Pull the message content and usage numbers out of a chat-completions body."""
        if error := body.get("error"):
            # OpenRouter reports upstream failures inside a 200 response.
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise LLMError(f"{self.name} returned an error: {message}", model=spec.model)

        choices = body.get("choices") or []
        if not choices:
            raise StructuredOutputError(f"{self.name} returned no choices.", model=spec.model)

        content = (choices[0].get("message") or {}).get("content") or ""
        if not content.strip():
            raise StructuredOutputError(f"{self.name} returned empty content.", model=spec.model)

        try:
            data = extract_json_object(content)
        except StructuredOutputError as exc:
            # Re-raise carrying the body so the orchestrator can run a repair pass.
            raise StructuredOutputError(
                f"{self.name}: {exc.message}", model=spec.model, raw_text=content
            ) from exc
        raw_usage = body.get("usage") or {}
        details = raw_usage.get("prompt_tokens_details") or {}
        return StructuredResponse(
            data=data,
            model=body.get("model") or spec.model,
            usage=TokenUsage(
                input_tokens=int(raw_usage.get("prompt_tokens", 0) or 0),
                output_tokens=int(raw_usage.get("completion_tokens", 0) or 0),
                cache_read_tokens=int(details.get("cached_tokens", 0) or 0),
            ),
            raw_text=content,
        )

    async def aclose(self) -> None:
        """Close the HTTP transport."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter, which fronts hundreds of models behind the same dialect."""

    name = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str | None = None,
        timeout: float = 120.0,
        app_url: str = "https://github.com/NabiBukhsh-AI/Resume-AI-Parser",
        app_title: str = "Resume AI Parser",
    ) -> None:
        super().__init__(
            api_key,
            base_url=base_url,
            timeout=timeout,
            # OpenRouter uses these for attribution on its public leaderboards.
            extra_headers={"HTTP-Referer": app_url, "X-Title": app_title},
        )

    def build_payload(self, request: StructuredRequest, spec: ModelSpec) -> dict[str, Any]:
        """Add OpenRouter's provider-routing preferences to the standard payload."""
        payload = super().build_payload(request, spec)
        # Ask OpenRouter to route only to upstreams that can honour a strict schema;
        # without this it may silently pick one that ignores response_format.
        payload["provider"] = {"require_parameters": True}
        return payload
