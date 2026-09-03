"""Test doubles shared across the suite."""

from __future__ import annotations

from typing import Any

from resume_parser.domain.results import TokenUsage
from resume_parser.llm.base import StructuredRequest, StructuredResponse
from resume_parser.settings import ModelSpec

__all__ = ["StubProvider"]


class StubProvider:
    """An :class:`LLMProvider` that replays a queued payload and records its calls."""

    name = "anthropic"

    def __init__(self, payloads: list[dict[str, Any]] | None = None) -> None:
        self.payloads = payloads or []
        self.calls: list[StructuredRequest] = []
        self.error: Exception | None = None
        self.closed = False

    async def generate(self, request: StructuredRequest, spec: ModelSpec) -> StructuredResponse:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        payload = self.payloads.pop(0) if self.payloads else {}
        return StructuredResponse(
            data=payload,
            model=spec.model,
            usage=TokenUsage(input_tokens=1200, output_tokens=800),
            raw_text="{}",
        )

    async def aclose(self) -> None:
        self.closed = True
