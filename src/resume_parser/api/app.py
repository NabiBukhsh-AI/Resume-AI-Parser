"""The FastAPI application factory.

Expensive, long-lived objects - the JSON Schema, the provider HTTP pools, the cache - are
built once in the lifespan and torn down cleanly on shutdown. The old module-level
``app = FastAPI()`` with import-time side effects made the app impossible to configure per
test and leaked connections on reload.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from resume_parser import __version__
from resume_parser.api.errors import install_exception_handlers
from resume_parser.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from resume_parser.api.routers import health, parse
from resume_parser.observability.logging import configure_logging, get_logger
from resume_parser.pipeline.parser import ResumeParsingService
from resume_parser.settings import Settings, get_settings

__all__ = ["create_app"]

logger = get_logger(__name__)

_DESCRIPTION = """\
Structured resume parsing powered by LLM constrained decoding.

* **POST `/v1/parse`** - one resume in, a validated structured record out.
* **POST `/v1/parse/batch`** - many resumes, parsed concurrently, with per-document errors.
* **POST `/v1/match`** - score a parsed resume against a job, with an explainable breakdown.
* **GET `/v1/schema`** - the exact JSON Schema the model is constrained to.

Numeric fields such as total years of experience are computed in Python from the extracted
dates, not asked of the model, so they are exact and reproducible.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured application instance.

    Args:
        settings: Override configuration. Defaults to the process settings singleton,
            which is what production uses; tests pass their own.
    """
    active = settings or get_settings()
    configure_logging(
        level=active.observability.log_level,
        json_logs=active.observability.json_logs,
        redact_pii=active.observability.redact_pii,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Construct the parsing service on startup; close its transports on shutdown."""
        service = ResumeParsingService(active)
        app.state.service = service

        usable = [spec.label for spec in active.configured_models()]
        logger.info(
            "application_started",
            version=__version__,
            environment=active.environment,
            models=usable or None,
            cache_enabled=active.cache.enabled,
        )
        if not usable:
            # Start anyway so /health/ready can report *why* the service is degraded -
            # crashing on boot gives an operator a restart loop and no diagnosis.
            logger.error(
                "no_models_configured",
                hint="Set ANTHROPIC_API_KEY, OPENROUTER_API_KEY or OPENAI_API_KEY.",
            )
        try:
            yield
        finally:
            await service.aclose()
            logger.info("application_stopped")

    app = FastAPI(
        title=active.app_name,
        version=__version__,
        description=_DESCRIPTION,
        lifespan=lifespan,
        root_path=active.server.root_path,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={
            "name": "Nabi Bukhsh Baloch",
            "url": "https://github.com/NabiBukhsh-AI/Resume-AI-Parser",
        },
        license_info={"name": "MIT", "identifier": "MIT"},
    )

    # Middleware runs bottom-up, so the request-context middleware is added last and
    # therefore runs first - every downstream log line carries the request id.
    if active.server.rate_limit_per_minute > 0:
        app.add_middleware(
            RateLimitMiddleware, requests_per_minute=active.server.rate_limit_per_minute
        )
    if active.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=active.server.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", "X-Response-Time-ms"],
        )
    app.add_middleware(RequestContextMiddleware)

    # Bound here rather than only in the lifespan so dependencies resolve the right
    # settings even when an ASGI transport skips startup (as test clients do).
    app.state.settings = active

    install_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(parse.router)
    return app


# Note: there is deliberately no module-level `app = create_app()`. Importing this module
# has no side effects, so tests can build an app with their own settings. Serve it with
# uvicorn's factory mode:
#
#     uvicorn resume_parser.api.app:create_app --factory
#
# or just `resume-parser serve`, which does the same thing.
