"""Tests for format detection and text extraction."""

from __future__ import annotations

import pytest

from resume_parser.domain.enums import DocumentFormat
from resume_parser.exceptions import (
    DocumentTooLargeError,
    EmptyDocumentError,
    InvalidDocumentError,
    ScannedDocumentError,
)
from resume_parser.extraction.detection import detect_format, sanitize_filename
from resume_parser.extraction.extractors import normalize_whitespace
from resume_parser.extraction.service import ExtractionService
from resume_parser.settings import ExtractionSettings


class TestFilenameSanitization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("resume.pdf", "resume.pdf"),
            ("../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32\\cfg", "cfg"),
            ("/absolute/path/cv.docx", "cv.docx"),
            ("", None),
            (None, None),
        ],
    )
    def test_directory_components_are_stripped(self, raw: str | None, expected: str | None) -> None:
        assert sanitize_filename(raw) == expected

    def test_illegal_characters_are_removed(self) -> None:
        assert sanitize_filename('cv<>:"|?*.pdf') == "cv.pdf"


class TestFormatDetection:
    def test_pdf_is_detected_from_magic_bytes(self, minimal_pdf_bytes: bytes) -> None:
        assert detect_format(minimal_pdf_bytes) is DocumentFormat.PDF

    def test_docx_is_detected_from_the_word_part(self, docx_resume_bytes: bytes) -> None:
        assert detect_format(docx_resume_bytes) is DocumentFormat.DOCX

    def test_plain_text_is_detected(self, text_resume_bytes: bytes) -> None:
        assert detect_format(text_resume_bytes) is DocumentFormat.TXT

    def test_content_beats_a_lying_extension(self, text_resume_bytes: bytes) -> None:
        """A .pdf extension on text bytes must not route to the PDF reader."""
        assert detect_format(text_resume_bytes, filename="resume.pdf") is DocumentFormat.TXT

    def test_non_docx_zip_is_rejected_with_a_useful_message(self) -> None:
        with pytest.raises(InvalidDocumentError, match=r"not a \.docx"):
            detect_format(b"PK\x03\x04" + b"\x00" * 200)

    def test_empty_input_is_rejected(self) -> None:
        with pytest.raises(InvalidDocumentError):
            detect_format(b"")

    def test_binary_garbage_is_rejected(self) -> None:
        with pytest.raises(InvalidDocumentError):
            detect_format(bytes(range(256)) * 4)

    def test_disallowed_format_is_rejected(self, text_resume_bytes: bytes) -> None:
        with pytest.raises(InvalidDocumentError, match="not accepted"):
            detect_format(text_resume_bytes, allowed=frozenset({DocumentFormat.PDF}))


class TestWhitespaceNormalization:
    def test_blank_line_runs_collapse(self) -> None:
        assert normalize_whitespace("a\n\n\n\n\nb") == "a\n\nb"

    def test_line_endings_are_unified(self) -> None:
        assert normalize_whitespace("a\r\nb\rc") == "a\nb\nc"

    def test_ligatures_are_folded(self) -> None:
        assert "finance" in normalize_whitespace("ﬁnance")


class TestExtractionService:
    def test_text_document_round_trips(self, text_resume_bytes: bytes) -> None:
        service = ExtractionService()
        result = service.extract(text_resume_bytes, filename="ada.txt")
        assert "Ada Lovelace" in result.text
        assert result.info.format is DocumentFormat.TXT
        assert result.info.size_bytes == len(text_resume_bytes)
        assert len(result.info.content_sha256) == 64

    def test_pdf_text_layer_is_read(self, minimal_pdf_bytes: bytes) -> None:
        service = ExtractionService()
        result = service.extract(minimal_pdf_bytes, filename="ada.pdf")
        assert "Ada Lovelace" in result.text
        assert result.info.page_count == 1

    def test_docx_tables_are_extracted(self, docx_resume_bytes: bytes) -> None:
        """Many resume templates put the whole skills block in a table."""
        service = ExtractionService()
        result = service.extract(docx_resume_bytes, filename="ada.docx")
        assert "Ada Lovelace" in result.text
        assert "PyTorch" in result.text
        assert "FastAPI" in result.text

    def test_oversized_upload_is_rejected(self, text_resume_bytes: bytes) -> None:
        service = ExtractionService(ExtractionSettings(max_file_size=len(text_resume_bytes) - 1))
        with pytest.raises(DocumentTooLargeError):
            service.extract(text_resume_bytes)

    def test_short_document_is_rejected_as_empty(self) -> None:
        service = ExtractionService()
        with pytest.raises(EmptyDocumentError):
            service.extract(b"too short")

    def test_pdf_without_a_text_layer_reports_ocr_is_needed(self) -> None:
        """A scanned resume must produce a specific, actionable error, not a bad parse."""
        import io

        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buffer = io.BytesIO()
        writer.write(buffer)

        service = ExtractionService()
        with pytest.raises(ScannedDocumentError, match="OCR"):
            service.extract(buffer.getvalue(), filename="scan.pdf")

    def test_allowed_formats_narrows_the_surface(self, text_resume_bytes: bytes) -> None:
        service = ExtractionService(ExtractionSettings(allowed_formats=["pdf"]))
        with pytest.raises(InvalidDocumentError):
            service.extract(text_resume_bytes)

    def test_unknown_configured_format_is_ignored(self) -> None:
        """A typo in configuration must narrow the surface, not crash a request."""
        service = ExtractionService(ExtractionSettings(allowed_formats=["pdf", "nonsense"]))
        assert DocumentFormat.PDF in service.allowed_formats
