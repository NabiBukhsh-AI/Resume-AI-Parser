"""Concrete extractors for PDF, DOCX and plain-text documents.

Each extractor is deliberately small and independent, and each one preserves the reading
order and layout cues that matter for a resume - section headers, bullet markers, and
tables, which many resume templates use for the entire skills block.
"""

from __future__ import annotations

import io
import re
import unicodedata

from resume_parser.domain.enums import DocumentFormat
from resume_parser.exceptions import ExtractionError
from resume_parser.extraction.base import ExtractedText

__all__ = ["DocxExtractor", "PdfExtractor", "PlainTextExtractor", "normalize_whitespace"]

# Collapses runs of blank lines to at most one, which keeps prompts compact without
# destroying the paragraph boundaries the model uses to segment sections.
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")
_SPACE_RUN_RE = re.compile(r"[ \t]{3,}")


def normalize_whitespace(text: str) -> str:
    """Normalize unicode, line endings and redundant whitespace."""
    # NFKC folds ligatures and full-width forms that PDF encoders emit into plain ASCII
    # equivalents, so 'ﬁnance' matches 'finance' during skill comparison.
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    # Non-breaking space and zero-width space, written as escapes so they stay visible
    # to anyone reading this file.
    normalized = normalized.replace("\u00a0", " ").replace("\u200b", "")
    normalized = _TRAILING_SPACE_RE.sub("\n", normalized)
    normalized = _SPACE_RUN_RE.sub("  ", normalized)
    normalized = _BLANK_RUN_RE.sub("\n\n", normalized)
    return normalized.strip()


class PdfExtractor:
    """Extracts a PDF's text layer with ``pypdf``.

    Scanned resumes have no text layer at all. Rather than sending an empty prompt to a
    model and paying for a hallucinated answer, the service layer inspects the character
    count and raises a specific 'needs OCR' error.
    """

    formats: tuple[DocumentFormat, ...] = (DocumentFormat.PDF,)

    def extract(self, data: bytes) -> ExtractedText:
        """Read every page of the PDF and concatenate the text in page order."""
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        warnings: list[str] = []
        try:
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted:
                # Resumes are occasionally saved with an empty owner password, which
                # pypdf can open transparently; a real password is a hard failure.
                try:
                    reader.decrypt("")
                except Exception as exc:
                    raise ExtractionError("PDF is password-protected and cannot be read.") from exc
            pages: list[str] = []
            for index, page in enumerate(reader.pages, start=1):
                try:
                    page_text = page.extract_text() or ""
                except Exception as exc:
                    warnings.append(f"Page {index} could not be decoded: {exc}")
                    continue
                if page_text.strip():
                    pages.append(page_text)
                else:
                    warnings.append(f"Page {index} contains no extractable text layer.")
        except ExtractionError:
            raise
        except PdfReadError as exc:
            raise ExtractionError(f"Malformed PDF: {exc}") from exc
        except Exception as exc:
            raise ExtractionError(f"Could not read PDF: {exc}") from exc

        return ExtractedText(
            text=normalize_whitespace("\n\n".join(pages)),
            format=DocumentFormat.PDF,
            page_count=len(reader.pages),
            warnings=warnings,
        )


class DocxExtractor:
    """Extracts paragraphs *and* tables from an OOXML Word document.

    Table extraction matters more than it looks: a large share of resume templates lay the
    entire skills or contact block out in an invisible table, and a paragraph-only reader
    silently drops it.
    """

    formats: tuple[DocumentFormat, ...] = (DocumentFormat.DOCX,)

    def extract(self, data: bytes) -> ExtractedText:
        """Walk the document body in order, flattening tables to pipe-delimited rows."""
        import docx
        from docx.document import Document as DocxDocument
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        try:
            document: DocxDocument = docx.Document(io.BytesIO(data))
        except Exception as exc:
            raise ExtractionError(f"Could not read DOCX: {exc}") from exc

        blocks: list[str] = []
        body = document.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                text = Paragraph(child, document).text.strip()
                if text:
                    blocks.append(text)
            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    # Word repeats merged cells across a row; collapse the duplicates.
                    deduped: list[str] = []
                    for cell in cells:
                        if cell and (not deduped or deduped[-1] != cell):
                            deduped.append(cell)
                    if deduped:
                        blocks.append(" | ".join(deduped))

        # Headers and footers frequently carry contact details on designed templates.
        for section in document.sections:
            for container in (section.header, section.footer):
                for paragraph in container.paragraphs:
                    text = paragraph.text.strip()
                    if text and text not in blocks:
                        blocks.append(text)

        return ExtractedText(
            text=normalize_whitespace("\n".join(blocks)),
            format=DocumentFormat.DOCX,
            warnings=[],
        )


class PlainTextExtractor:
    """Decodes ``.txt`` and ``.md`` uploads."""

    formats: tuple[DocumentFormat, ...] = (DocumentFormat.TXT, DocumentFormat.MARKDOWN)

    #: Tried in order. UTF-8 first, then the two encodings resume exports actually use.
    _ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")

    def extract(self, data: bytes) -> ExtractedText:
        """Decode ``data`` using the first encoding that round-trips cleanly."""
        warnings: list[str] = []
        for encoding in self._ENCODINGS:
            try:
                decoded = data.decode(encoding)
            except UnicodeDecodeError:
                continue
            if encoding != "utf-8":
                warnings.append(f"Decoded using {encoding} after UTF-8 failed.")
            return ExtractedText(
                text=normalize_whitespace(decoded),
                format=DocumentFormat.TXT,
                warnings=warnings,
            )
        raise ExtractionError("Text file could not be decoded with any supported encoding.")
