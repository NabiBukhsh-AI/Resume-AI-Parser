"""PII redaction for logs and error payloads.

Resumes are personal data. Anything that reaches a log aggregator, an error tracker or a
support ticket should not carry a candidate's email address, phone number or the raw text
of their CV. These helpers are applied by a structlog processor, so redaction happens once
at the edge instead of relying on every call site to remember.

This is a defence-in-depth measure, not a compliance guarantee: it catches the common
identifier shapes, and the safest thing remains not logging document text at all.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["redact_mapping", "redact_text", "truncate"]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Loose international phone shape: optional +, then 7-20 digits with common separators.
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,20}\d(?!\w)")
_URL_CREDENTIALS_RE = re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")

#: Keys whose values are replaced wholesale rather than pattern-matched.
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "anthropic_api_key",
        "openai_api_key",
        "openrouter_api_key",
        "password",
        "secret",
        "token",
        "x-api-key",
    }
)

#: Keys that hold document bodies - truncated hard, since they are almost entirely PII.
_BULK_TEXT_KEYS = frozenset({"text", "content", "resume_text", "document_text", "prompt", "body"})

_MASK = "[redacted]"


def redact_text(value: str) -> str:
    """Mask emails, phone numbers and inline URL credentials inside ``value``."""
    masked = _EMAIL_RE.sub("[email]", value)
    masked = _PHONE_RE.sub("[phone]", masked)
    return _URL_CREDENTIALS_RE.sub(_MASK, masked)


def truncate(value: str, limit: int = 120) -> str:
    """Shorten ``value`` to ``limit`` characters, noting how much was dropped."""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... [+{len(value) - limit} chars]"


def redact_mapping(data: dict[str, Any], *, text_limit: int = 120) -> dict[str, Any]:
    """Return a copy of ``data`` safe to write to a log sink.

    Sensitive keys are masked outright, bulk-text keys are truncated, and every remaining
    string is scanned for identifier patterns. Nested mappings and sequences are walked.
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        lowered = key.lower()
        if lowered in _SENSITIVE_KEYS:
            out[key] = _MASK
        elif lowered in _BULK_TEXT_KEYS and isinstance(value, str):
            out[key] = truncate(redact_text(value), text_limit)
        else:
            out[key] = _redact_value(value, text_limit)
    return out


def _redact_value(value: Any, text_limit: int) -> Any:
    if isinstance(value, str):
        return truncate(redact_text(value), max(text_limit, 512))
    if isinstance(value, dict):
        return redact_mapping(value, text_limit=text_limit)
    if isinstance(value, list | tuple):
        return [_redact_value(item, text_limit) for item in value]
    return value
