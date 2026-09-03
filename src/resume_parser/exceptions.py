"""Domain exception hierarchy.

Every failure the application can produce is one of these. The API layer maps them to
RFC 9457 problem responses in :mod:`resume_parser.api.errors`, so HTTP status codes are
decided in exactly one place instead of being scattered across route handlers.
"""

from __future__ import annotations

from typing import Any


class ResumeParserError(Exception):
    """Base class for every error raised by this package."""

    #: Default HTTP status used when the error escapes to the API layer.
    status_code: int = 500
    #: Stable, machine-readable slug clients can branch on.
    code: str = "internal_error"

    def __init__(self, message: str, /, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context


# --------------------------------------------------------------------------- input


class InvalidDocumentError(ResumeParserError):
    """The uploaded bytes are not a document we can read."""

    status_code = 415
    code = "invalid_document"


class DocumentTooLargeError(ResumeParserError):
    """The document exceeds the configured size ceiling."""

    status_code = 413
    code = "document_too_large"


class EmptyDocumentError(ResumeParserError):
    """The document parsed successfully but yielded no usable text."""

    status_code = 422
    code = "empty_document"


class ScannedDocumentError(EmptyDocumentError):
    """A PDF that is almost certainly a scan/image and needs OCR."""

    code = "scanned_document"


class ExtractionError(ResumeParserError):
    """The document is a known format but could not be decoded."""

    status_code = 422
    code = "extraction_failed"


# ----------------------------------------------------------------------------- llm


class LLMError(ResumeParserError):
    """Base class for provider-side failures."""

    status_code = 502
    code = "llm_error"


class ProviderNotConfiguredError(LLMError):
    """No credentials or no usable model for the requested provider."""

    status_code = 503
    code = "provider_not_configured"


class LLMTimeoutError(LLMError):
    """The provider did not answer within the configured deadline."""

    status_code = 504
    code = "llm_timeout"


class LLMRateLimitError(LLMError):
    """The provider rejected the call with a rate-limit response."""

    status_code = 429
    code = "llm_rate_limited"


class StructuredOutputError(LLMError):
    """The provider replied, but the payload did not satisfy the schema."""

    status_code = 502
    code = "invalid_structured_output"


class AllProvidersFailedError(LLMError):
    """Every model in the fallback chain failed."""

    status_code = 502
    code = "all_providers_failed"


# ------------------------------------------------------------------------ platform


class AuthenticationError(ResumeParserError):
    """Missing or incorrect API key on an authenticated route."""

    status_code = 401
    code = "unauthorized"


class RateLimitExceededError(ResumeParserError):
    """The caller exhausted their local request budget."""

    status_code = 429
    code = "rate_limited"


class ConfigurationError(ResumeParserError):
    """The application is mis-configured and cannot serve requests."""

    status_code = 500
    code = "configuration_error"
