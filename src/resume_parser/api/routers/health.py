"""Liveness, readiness and service-metadata routes.

Liveness and readiness are separated because they answer different questions for an
orchestrator: liveness asks "is the process wedged, should you restart it?", readiness asks
"can this instance serve traffic right now?". Collapsing them into one endpoint causes
Kubernetes to restart healthy pods whenever a dependency is briefly unavailable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from resume_parser import __version__
from resume_parser.api.dependencies import CurrentSettings, ParsingService
from resume_parser.api.schemas import HealthResponse, ReadinessResponse

__all__ = ["router"]

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: CurrentSettings) -> HealthResponse:
    """Report that the process is up. Never touches a dependency."""
    return HealthResponse(version=__version__, environment=settings.environment)


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def readiness(
    settings: CurrentSettings, service: ParsingService, response: Response
) -> ReadinessResponse:
    """Report whether a parse could actually be served.

    Returns 503 when no configured model has credentials, which is the one condition that
    makes every parse fail regardless of input.
    """
    configured = settings.configured_models()
    available = [spec.label for spec in configured]
    unavailable = [spec.label for spec in settings.llm.models if spec.label not in set(available)]
    ready = bool(available)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if ready else "degraded",
        version=__version__,
        providers_configured=available,
        models_unavailable=unavailable,
        cache=service.cache.stats,
        supported_formats=sorted(settings.extraction.allowed_formats),
    )


@router.get("/v1/schema", summary="The JSON Schema used for extraction")
async def extraction_schema(service: ParsingService) -> dict[str, Any]:
    """Return the strict JSON Schema the model is constrained to.

    Published so integrators can generate their own types from the same contract the model
    is held to, rather than reverse-engineering it from example responses.
    """
    return dict(service.resume_schema)
