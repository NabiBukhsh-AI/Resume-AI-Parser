"""FastAPI dependencies: settings, the parsing service, and authentication."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from resume_parser.exceptions import AuthenticationError
from resume_parser.pipeline.parser import ResumeParsingService
from resume_parser.settings import Settings, get_settings

__all__ = ["CurrentSettings", "ParsingService", "get_app_settings", "require_api_key"]

_api_key_scheme = APIKeyHeader(
    name="x-api-key",
    auto_error=False,
    description="Shared secret. Required only when the server is configured with one.",
)


def get_app_settings(request: Request) -> Settings:
    """Return the settings this application instance was built with.

    Resolving from ``app.state`` rather than the global singleton matters: an app created
    with explicit settings must be governed by *those* settings everywhere, or an override
    silently applies to the routes but not to authentication and limits.
    """
    settings = getattr(request.app.state, "settings", None)
    return settings if settings is not None else get_settings()


def get_service(request: Request) -> ResumeParsingService:
    """Return the process-wide parsing service built during application startup.

    Constructing it per request would rebuild the JSON Schema and discard HTTP connection
    pools on every call.
    """
    service = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover - lifespan always sets this
        msg = "Parsing service is not initialised"
        raise RuntimeError(msg)
    return service  # type: ignore[no-any-return]


async def require_api_key(
    provided: Annotated[str | None, Depends(_api_key_scheme)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    """Enforce the shared secret when one is configured.

    Comparison uses :func:`secrets.compare_digest`. The original used ``!=``, which leaks
    the number of matching leading characters through its timing and lets an attacker
    recover the key byte by byte.
    """
    if settings.api_key is None:
        return
    expected = settings.api_key.get_secret_value()
    if not provided or not secrets.compare_digest(provided, expected):
        raise AuthenticationError("A valid 'x-api-key' header is required.")


CurrentSettings = Annotated[Settings, Depends(get_app_settings)]
ParsingService = Annotated[ResumeParsingService, Depends(get_service)]
