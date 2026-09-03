"""Request correlation, access logging and a lightweight rate limiter."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from resume_parser.api.errors import problem_response
from resume_parser.observability.logging import (
    bind_request_context,
    clear_request_context,
    get_logger,
    new_request_id,
)

__all__ = ["RateLimitMiddleware", "RequestContextMiddleware"]

logger = get_logger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns each request an id, binds it to the log context, and times the call.

    An id supplied by an upstream proxy is honoured so a trace survives across services.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or new_request_id()
        request.state.request_id = request_id

        clear_request_context()
        bind_request_context(request_id=request_id, method=request.method, path=request.url.path)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers own the response; we only record the timing here.
            logger.exception(
                "request_errored",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
        finally:
            clear_request_context()

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[_REQUEST_ID_HEADER] = request_id
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        if request.url.path not in ("/health", "/health/live"):
            logger.info(
                "request_completed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """A per-client sliding-window limiter.

    In-process and therefore per-worker: with four workers the effective ceiling is four
    times the configured value. That is the right trade for a single-service deployment
    and a deliberate limitation - a multi-instance deployment should put a shared limiter
    (Redis, or the ingress) in front and set ``rate_limit_per_minute`` to 0 here.
    """

    def __init__(self, app: Callable[..., object], *, requests_per_minute: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._limit = requests_per_minute
        self._window = 60.0
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _client_key(self, request: Request) -> str:
        """Identify the caller by API key where present, else by source address."""
        if api_key := request.headers.get("x-api-key"):
            # Never key on the secret itself - a log or a dump would expose it.
            return f"key:{hash(api_key) & 0xFFFFFFFF:08x}"
        client = request.client
        return f"ip:{client.host}" if client else "ip:unknown"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if self._limit <= 0 or request.url.path.startswith("/health"):
            return await call_next(request)

        key = self._client_key(request)
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self._window:
            hits.popleft()

        if len(hits) >= self._limit:
            retry_after = max(1, int(self._window - (now - hits[0])))
            logger.warning("rate_limited", client=key, limit=self._limit)
            rejection = problem_response(
                status_code=429,
                code="rate_limited",
                detail=(
                    f"Rate limit of {self._limit} requests per minute exceeded. "
                    f"Retry in {retry_after}s."
                ),
                request_id=getattr(request.state, "request_id", None),
            )
            rejection.headers["Retry-After"] = str(retry_after)
            return rejection

        hits.append(now)
        # Bound memory: drop client windows that have fully aged out.
        if len(self._hits) > 10_000:
            self._prune(now)

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self._limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, self._limit - len(hits)))
        return response

    def _prune(self, now: float) -> None:
        """Evict client entries whose window is empty."""
        stale = [
            key for key, hits in self._hits.items() if not hits or now - hits[-1] > self._window
        ]
        for key in stale:
            del self._hits[key]
