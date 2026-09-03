"""Extractor protocol and the value object every extractor returns.

Extractors operate on ``bytes``, never on file paths. The previous implementation wrote
each upload to an ``uploads/`` directory using the client-supplied filename, then deleted
it in a ``finally`` block - which meant a path-traversal surface, litter on every crash,
and needless disk I/O on the hot path. Nothing here touches the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from resume_parser.domain.enums import DocumentFormat

__all__ = ["ExtractedText", "TextExtractor"]


@dataclass(slots=True)
class ExtractedText:
    """Text recovered from a document, plus what we learned while recovering it."""

    text: str
    """The extracted plain text, normalized to ``\\n`` line endings."""

    format: DocumentFormat
    """Format the extractor handled."""

    page_count: int | None = None
    """Pages processed, for paginated formats."""

    warnings: list[str] = field(default_factory=list)
    """Non-fatal observations, e.g. pages that yielded no text layer."""

    @property
    def character_count(self) -> int:
        """Length of the extracted text."""
        return len(self.text)


@runtime_checkable
class TextExtractor(Protocol):
    """Turns the bytes of one document format into text.

    Implementations are stateless and cheap to construct, so the registry keeps a single
    instance of each and shares it across requests.
    """

    #: Formats this extractor claims.
    formats: tuple[DocumentFormat, ...]

    def extract(self, data: bytes) -> ExtractedText:
        """Decode ``data`` into text.

        Raises:
            ExtractionError: The bytes are the right format but cannot be decoded.
        """
        ...
