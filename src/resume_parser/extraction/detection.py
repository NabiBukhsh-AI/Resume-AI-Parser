"""Format detection from content, with the filename as a hint only.

The old code trusted the file extension and used ``python-magic-bin``, a Windows-only
binary wheel pinned to 0.4.14 that makes the project unbuildable in a Linux container.
This module sniffs magic bytes with ``puremagic`` - pure Python, same behaviour on every
platform - and falls back to structural checks, so a ``.pdf`` that is really a ZIP is
rejected rather than handed to the PDF reader.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import puremagic

from resume_parser.domain.enums import DocumentFormat
from resume_parser.exceptions import InvalidDocumentError

__all__ = ["detect_format", "sanitize_filename"]

_EXTENSION_MAP: dict[str, DocumentFormat] = {
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".txt": DocumentFormat.TXT,
    ".text": DocumentFormat.TXT,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
}

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
# Present in the central directory of every OOXML word-processing package.
_DOCX_MARKER = b"word/document.xml"


def sanitize_filename(filename: str | None) -> str | None:
    """Reduce a client-supplied filename to a safe basename.

    We only ever use the result for logging and response metadata, never to build a path,
    but stripping directory components keeps traversal sequences out of logs and prevents
    a stored filename from being replayed as a path by downstream consumers.
    """
    if not filename:
        return None
    # Normalize both separators before taking the final component.
    candidate = PurePosixPath(filename.replace("\\", "/")).name
    cleaned = "".join(ch for ch in candidate if ch.isprintable() and ch not in '<>:"|?*')
    cleaned = cleaned.strip(". ")
    return cleaned[:255] or None


def _looks_like_docx(data: bytes) -> bool:
    """True when the ZIP container carries a Word document part."""
    if not data.startswith(_ZIP_MAGIC):
        return False
    # The central directory lives at the tail; scanning it avoids a full unzip.
    return _DOCX_MARKER in data[-65_536:] or _DOCX_MARKER in data[:65_536]


def _looks_like_text(data: bytes) -> bool:
    """True when the bytes decode as UTF-8 and contain no NUL control bytes."""
    sample = data[:8192]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def detect_format(
    data: bytes,
    *,
    filename: str | None = None,
    allowed: frozenset[DocumentFormat] | None = None,
) -> DocumentFormat:
    """Identify the format of ``data``.

    Content wins over the filename: the extension is consulted only to disambiguate cases
    the bytes cannot settle on their own, such as Markdown versus plain text.

    Args:
        data: Raw document bytes.
        filename: Original filename, used as a hint.
        allowed: Restrict the result to this set; anything else is rejected.

    Raises:
        InvalidDocumentError: The bytes are not a supported document, or the detected
            format is not in ``allowed``.
    """
    if not data:
        raise InvalidDocumentError("Uploaded document is empty")

    detected: DocumentFormat | None = None

    if data.startswith(_PDF_MAGIC):
        detected = DocumentFormat.PDF
    elif _looks_like_docx(data):
        detected = DocumentFormat.DOCX
    elif data.startswith(_ZIP_MAGIC):
        # A ZIP that is not a Word package - most likely .doc, .odt, .pages or an archive.
        raise InvalidDocumentError(
            "File is a ZIP archive but not a .docx document. "
            "Legacy .doc, .odt and .pages files are not supported; export to PDF or DOCX.",
            filename=sanitize_filename(filename),
        )

    if detected is None:
        # puremagic is a second opinion for formats without a marker we check directly.
        try:
            guesses = puremagic.magic_string(data)
        except (puremagic.PureError, ValueError):
            guesses = []
        for guess in guesses:
            mapped = _EXTENSION_MAP.get(str(guess.extension).lower())
            if mapped is not None:
                detected = mapped
                break

    if detected is None and _looks_like_text(data):
        suffix = PurePosixPath(sanitize_filename(filename) or "").suffix.lower()
        detected = _EXTENSION_MAP.get(suffix, DocumentFormat.TXT)
        if detected not in (DocumentFormat.TXT, DocumentFormat.MARKDOWN):
            detected = DocumentFormat.TXT

    if detected is None:
        raise InvalidDocumentError(
            "Unrecognised file type. Supported formats: PDF, DOCX, TXT, Markdown.",
            filename=sanitize_filename(filename),
        )

    if allowed is not None and detected not in allowed:
        supported = ", ".join(sorted(fmt.value for fmt in allowed))
        raise InvalidDocumentError(
            f"Documents of type '{detected.value}' are not accepted. Supported: {supported}.",
            detected_format=detected.value,
            filename=sanitize_filename(filename),
        )

    return detected
