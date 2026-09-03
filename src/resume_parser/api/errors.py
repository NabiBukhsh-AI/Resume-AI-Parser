"""Uniform error responses, in RFC 9457 problem-details form.

Every failure leaves the API with the same body shape, a stable ``code`` clients can
branch on, and the request id needed to find the matching log line. The route handlers
contain no try/except at all - the old design wrapped each route in
``except ValueError -> 400 / except Exception -> 500``, which flattened a rate limit, a
scanned PDF and a mis-typed API key into one indistinguishable 500.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from resume_parser.exceptions import ResumeParserError
from resume_parser.observability.logging import get_logger

__all__ = ["install_exception_handlers", "problem_response"]

logger = get_logger(__name__)

_MEDIA_TYPE = "application/problem+json"

# Starlette renamed this constant; resolve it once so the module works on either version.
_HTTP_422 = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)


def problem_response(
    *,
    status_code: int,
    code: str,
    detail: str,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build one problem-details response."""
    body: dict[str, Any] = {
        "type": f"https://docs.resume-ai-parser.dev/errors/{code}",
        "title": code.replace("_", " ").title(),
        "status": status_code,
        "code": code,
        "detail": detail,
    }
    if request_id:
        body["request_id"] = request_id
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body, media_type=_MEDIA_TYPE)


def _request_id(request: Request) -> str | None:
    """Read the correlation id the middleware attached to this request."""
    return getattr(request.state, "request_id", None)


def install_exception_handlers(app: FastAPI) -> None:
    """Register handlers translating every error class into a problem response."""

    @app.exception_handler(ResumeParserError)
    async def _domain_error(request: Request, exc: ResumeParserError) -> JSONResponse:
        # 5xx is our fault and gets a stack trace; 4xx is the caller's and does not.
        log = logger.error if exc.status_code >= 500 else logger.info
        log(
            "request_failed",
            code=exc.code,
            status=exc.status_code,
            reason=exc.message,
            path=request.url.path,
            **exc.context,
        )
        return problem_response(
            status_code=exc.status_code,
            code=exc.code,
            detail=exc.message,
            request_id=_request_id(request),
            extra={k: v for k, v in exc.context.items() if _is_jsonable(v)},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem_response(
            status_code=_HTTP_422,
            code="validation_error",
            detail="The request did not match the expected schema.",
            request_id=_request_id(request),
            extra={"errors": _serializable_errors(exc)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem_response(
            status_code=exc.status_code,
            code=_STATUS_CODES.get(exc.status_code, "http_error"),
            detail=str(exc.detail),
            request_id=_request_id(request),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", path=request.url.path, error=str(exc))
        # Never leak an internal message to the caller; the request id links to the log.
        return problem_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            detail="An unexpected error occurred. Quote the request id when reporting it.",
            request_id=_request_id(request),
        )


_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "document_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
    503: "service_unavailable",
}


def _is_jsonable(value: Any) -> bool:
    """True when ``value`` can be serialized into a problem body as-is."""
    return isinstance(value, str | int | float | bool | list | dict | type(None))


def _serializable_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Render Pydantic's error list as plain JSON.

    A ``model_validator`` that raises ``ValueError`` puts the exception object itself into
    each error's ``ctx``, which is not JSON-serializable - so the 422 would itself fail to
    render and surface as a 500. Dropping ``ctx`` and the echoed ``input`` also avoids
    reflecting the caller's payload (potentially a whole resume) back in an error body.
    """
    cleaned: list[dict[str, Any]] = []
    for error in exc.errors()[:10]:
        cleaned.append(
            {
                "type": str(error.get("type", "")),
                "loc": [str(part) for part in error.get("loc", ())],
                "msg": str(error.get("msg", "")),
            }
        )
    return cleaned
