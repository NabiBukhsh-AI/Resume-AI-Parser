"""Structured logging setup.

The original project shipped a ``utils/logging.py`` that shadowed the standard library's
``logging`` module and returned a bare ``StreamHandler``. This replaces it with structlog:
key/value events, a request-scoped correlation id bound via context variables, JSON output
for production sinks, and a redaction processor so candidate PII never reaches the sink.

Call :func:`configure_logging` exactly once at process start (the API lifespan and the CLI
both do). Everywhere else, just call :func:`get_logger`.
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars
from structlog.typing import EventDict, WrappedLogger

from resume_parser.observability.redaction import redact_mapping

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

__all__ = [
    "bind_request_context",
    "clear_request_context",
    "configure_logging",
    "get_logger",
    "new_request_id",
]

_configured = False


def _redaction_processor(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Strip PII and secrets from every event before it is rendered."""
    return redact_mapping(dict(event_dict))


def configure_logging(
    *,
    level: str = "INFO",
    json_logs: bool = False,
    redact_pii: bool = True,
    force: bool = False,
) -> None:
    """Install the structlog + stdlib logging pipeline.

    Args:
        level: Root log level name.
        json_logs: Emit newline-delimited JSON instead of coloured console output.
        redact_pii: Run the redaction processor. Leave on unless you are debugging
            locally against documents you own.
        force: Reconfigure even if this has already run. Used by tests.
    """
    global _configured
    if _configured and not force:
        return

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]
    if redact_pii:
        shared_processors.append(_redaction_processor)

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib loggers (uvicorn, httpx, anthropic) through the same handler so the
    # output of a running service is one coherent stream rather than two formats.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric_level)

    # These are chatty at INFO and add nothing over our own request logs.
    for noisy in ("httpx", "httpcore", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(max(numeric_level, logging.WARNING))

    _configured = True


def get_logger(name: str | None = None) -> BoundLogger:
    """Return a bound structlog logger, configuring logging first if needed."""
    if not _configured:
        configure_logging()
    return structlog.stdlib.get_logger(name)


def new_request_id() -> str:
    """Generate a short correlation id for one inbound request or CLI invocation."""
    return uuid.uuid4().hex[:16]


def bind_request_context(**values: Any) -> None:
    """Bind key/values onto every log event emitted by the current task."""
    bind_contextvars(**values)


def clear_request_context() -> None:
    """Drop context bound by :func:`bind_request_context`."""
    clear_contextvars()
