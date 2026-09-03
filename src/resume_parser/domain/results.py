"""Envelopes that carry a result plus the metadata needed to operate the system.

Returning a bare resume object is fine for a demo. In production you also want to know
which model produced it, what it cost, how long it took, and whether it came from cache -
otherwise you cannot debug a bad parse or forecast a bill.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from resume_parser.domain.enums import DocumentFormat
from resume_parser.domain.resume import Resume

__all__ = ["DocumentInfo", "ParseResult", "TokenUsage", "UsageMetadata"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TokenUsage(_Base):
    """Token accounting for a single provider call."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        """Billable tokens across every bucket."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class UsageMetadata(_Base):
    """Everything an operator needs to reason about one parse."""

    provider: str = Field(description="Provider that produced the result.")
    model: str = Field(description="Model identifier used.")
    tokens: TokenUsage = Field(default_factory=TokenUsage, description="Token counts.")
    estimated_cost_usd: float | None = Field(
        default=None, ge=0, description="Cost estimate from the configured price table."
    )
    latency_ms: int = Field(default=0, ge=0, description="Wall-clock time for the provider call.")
    attempts: int = Field(default=1, ge=1, description="Provider calls made, including retries.")
    cached: bool = Field(default=False, description="True when served from the result cache.")
    fallback_used: bool = Field(
        default=False, description="True when the primary model failed and a fallback answered."
    )
    prompt_version: str = Field(default="", description="Version tag of the prompt template.")


class DocumentInfo(_Base):
    """Provenance of the input document."""

    filename: str | None = Field(default=None, description="Original filename, if supplied.")
    format: DocumentFormat = Field(description="Detected document format.")
    size_bytes: int = Field(ge=0, description="Size of the uploaded bytes.")
    content_sha256: str = Field(description="Digest of the raw bytes, for de-duplication.")
    text_characters: int = Field(ge=0, description="Characters of text extracted.")
    page_count: int | None = Field(default=None, ge=0, description="Pages, for paginated formats.")
    truncated: bool = Field(
        default=False, description="True when text was trimmed to fit the model's input budget."
    )


class ParseResult(_Base):
    """The complete response for one parsed resume."""

    resume: Resume = Field(description="The structured, normalized and enriched resume.")
    document: DocumentInfo = Field(description="Where the data came from.")
    usage: UsageMetadata = Field(description="Model, cost and timing metadata.")
    warnings: list[str] = Field(
        default_factory=list, description="Non-fatal issues worth surfacing to the caller."
    )
    parsed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="UTC completion timestamp."
    )
