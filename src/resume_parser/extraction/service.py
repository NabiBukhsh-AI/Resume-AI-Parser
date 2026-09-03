"""The extraction entry point: bytes in, validated text out.

This is the only module the rest of the application talks to for document handling. It
enforces the size ceiling, detects the format, dispatches to the right extractor, and
applies the emptiness and scanned-document checks in one place.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from resume_parser.domain.enums import DocumentFormat
from resume_parser.domain.results import DocumentInfo
from resume_parser.exceptions import (
    DocumentTooLargeError,
    EmptyDocumentError,
    ExtractionError,
    ScannedDocumentError,
)
from resume_parser.extraction.base import ExtractedText, TextExtractor
from resume_parser.extraction.detection import detect_format, sanitize_filename
from resume_parser.extraction.extractors import (
    DocxExtractor,
    PdfExtractor,
    PlainTextExtractor,
)
from resume_parser.settings import ExtractionSettings

__all__ = ["DocumentText", "ExtractionService"]


@dataclass(slots=True)
class DocumentText:
    """Extracted text bundled with the provenance record the API returns."""

    text: str
    info: DocumentInfo
    warnings: list[str]


def _default_registry() -> dict[DocumentFormat, TextExtractor]:
    """Build the format-to-extractor map from the bundled extractors."""
    registry: dict[DocumentFormat, TextExtractor] = {}
    available: tuple[TextExtractor, ...] = (PdfExtractor(), DocxExtractor(), PlainTextExtractor())
    for extractor in available:
        for fmt in extractor.formats:
            registry[fmt] = extractor
    return registry


class ExtractionService:
    """Validates and decodes uploaded documents.

    Extractors are injectable so a deployment can add OCR, HTML or RTF support without
    touching this class - register an object satisfying :class:`TextExtractor` and the
    dispatch table picks it up.
    """

    def __init__(
        self,
        settings: ExtractionSettings | None = None,
        extractors: dict[DocumentFormat, TextExtractor] | None = None,
    ) -> None:
        self._settings = settings or ExtractionSettings()
        self._extractors = extractors if extractors is not None else _default_registry()

    @property
    def allowed_formats(self) -> frozenset[DocumentFormat]:
        """Formats this instance will accept, intersected with what it can decode.

        Configuration may name a format we have no extractor for; taking the intersection
        means a typo in ``allowed_formats`` narrows the surface rather than crashing at
        request time.
        """
        configured = {
            fmt
            for value in self._settings.allowed_formats
            if (fmt := _as_format(value)) is not None
        }
        return frozenset(configured & set(self._extractors))

    def register(self, extractor: TextExtractor) -> None:
        """Add or replace the extractor for every format it declares."""
        for fmt in extractor.formats:
            self._extractors[fmt] = extractor

    def extract(self, data: bytes, *, filename: str | None = None) -> DocumentText:
        """Validate ``data`` and return its text plus a :class:`DocumentInfo` record.

        Raises:
            DocumentTooLargeError: The upload exceeds ``extraction.max_file_size``.
            InvalidDocumentError: The bytes are not a supported document type.
            ExtractionError: The format is supported but the bytes could not be decoded.
            EmptyDocumentError: Decoding produced too little text to parse.
            ScannedDocumentError: A PDF with no usable text layer.
        """
        size = len(data)
        if size > self._settings.max_file_size:
            raise DocumentTooLargeError(
                f"Document is {size} bytes; the limit is {self._settings.max_file_size} bytes.",
                size_bytes=size,
                limit_bytes=self._settings.max_file_size,
            )

        safe_name = sanitize_filename(filename)
        detected = detect_format(data, filename=safe_name, allowed=self.allowed_formats)

        extractor = self._extractors.get(detected)
        if extractor is None:  # pragma: no cover - guarded by allowed_formats
            raise ExtractionError(f"No extractor registered for '{detected.value}'.")

        extracted: ExtractedText = extractor.extract(data)
        self._assert_usable(extracted, detected)

        info = DocumentInfo(
            filename=safe_name,
            format=detected,
            size_bytes=size,
            content_sha256=hashlib.sha256(data).hexdigest(),
            text_characters=extracted.character_count,
            page_count=extracted.page_count,
            truncated=False,
        )
        return DocumentText(text=extracted.text, info=info, warnings=list(extracted.warnings))

    def _assert_usable(self, extracted: ExtractedText, fmt: DocumentFormat) -> None:
        """Reject documents that decoded but carry no meaningful content."""
        if extracted.character_count >= self._settings.min_text_characters:
            return
        if fmt is DocumentFormat.PDF:
            raise ScannedDocumentError(
                "This PDF has no extractable text layer, which usually means it is a scan "
                "or an image export. Re-save it as a text PDF, or run OCR before uploading.",
                characters_found=extracted.character_count,
            )
        raise EmptyDocumentError(
            "The document did not contain enough text to parse "
            f"({extracted.character_count} characters).",
            characters_found=extracted.character_count,
        )


def _as_format(value: str) -> DocumentFormat | None:
    """Coerce a configured string to a :class:`DocumentFormat`, or ``None`` if unknown."""
    try:
        return DocumentFormat(value)
    except ValueError:
        return None
