"""The provider abstraction.

Everything above this layer is provider-agnostic: the pipeline asks for "structured JSON
matching this schema" and gets back a validated payload plus usage numbers. Adding a
provider means implementing one method, not touching the parser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from resume_parser.domain.results import TokenUsage
from resume_parser.settings import ModelSpec

__all__ = ["LLMProvider", "StructuredRequest", "StructuredResponse"]


@dataclass(slots=True)
class StructuredRequest:
    """One request for schema-constrained JSON."""

    system: str
    """System prompt. Held stable across calls so it can be cached provider-side."""

    user: str
    """The user turn - in practice, the resume text plus task framing."""

    schema: dict[str, Any]
    """Strict JSON Schema the response must satisfy."""

    schema_name: str = "structured_output"
    """Identifier some providers require alongside the schema."""

    max_output_tokens: int = 16_000
    effort: str = "medium"
    """Reasoning effort, for providers that expose it. Ignored elsewhere."""

    metadata: dict[str, str] = field(default_factory=dict)
    """Free-form tags forwarded to providers that support request metadata."""


@dataclass(slots=True)
class StructuredResponse:
    """A provider's answer, already parsed into a JSON object."""

    data: dict[str, Any]
    """The decoded payload. Schema-valid as far as the provider enforced it."""

    model: str
    """The model that actually answered, which may differ from the one requested."""

    usage: TokenUsage = field(default_factory=TokenUsage)
    raw_text: str = ""
    """The unparsed response body, retained for the repair pass and for debugging."""


@runtime_checkable
class LLMProvider(Protocol):
    """Produces schema-constrained JSON from a prompt.

    Implementations own their own transport and are responsible for translating provider
    errors into the :mod:`resume_parser.exceptions` hierarchy, so the retry policy above
    them can make decisions without knowing which vendor it is talking to.
    """

    #: Registry key, matching ``ModelSpec.provider``.
    name: str

    async def generate(self, request: StructuredRequest, spec: ModelSpec) -> StructuredResponse:
        """Call the model and return validated JSON.

        Raises:
            LLMRateLimitError: The provider signalled rate limiting; retryable.
            LLMTimeoutError: The call exceeded its deadline; retryable.
            StructuredOutputError: A response arrived but was not usable JSON.
            LLMError: Any other provider-side failure.
        """
        ...

    async def aclose(self) -> None:
        """Release any transport resources held by the provider."""
        ...
