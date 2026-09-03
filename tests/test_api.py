"""HTTP contract tests.

These exercise the API through a real ASGI transport, so routing, dependency wiring,
middleware, authentication and error translation are all covered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from resume_parser.api.app import create_app
from resume_parser.api.dependencies import get_service
from resume_parser.llm.client import LLMClient
from resume_parser.pipeline.parser import ResumeParsingService
from resume_parser.settings import CacheSettings, LLMSettings, ModelSpec, Settings
from stubs import StubProvider


@pytest.fixture
def api_settings() -> Settings:
    """Settings for the API tests: no rate limiter, no cache, one stub model."""
    return Settings(
        anthropic_api_key="test-key",
        llm=LLMSettings(
            models=[ModelSpec(provider="anthropic", model="claude-opus-5")],
            max_retries=0,
            retry_base_delay=0.001,
        ),
        cache=CacheSettings(enabled=False),
    )


def _build_app(settings: Settings, provider: StubProvider) -> Any:
    """Create an app whose parsing service is backed by ``provider``."""
    app = create_app(settings)
    service = ResumeParsingService(
        settings, llm=LLMClient(settings, providers={"anthropic": provider})
    )
    app.dependency_overrides[get_service] = lambda: service
    return app


@pytest.fixture
async def client(
    api_settings: Settings, sample_resume_payload: dict[str, Any]
) -> AsyncIterator[httpx.AsyncClient]:
    provider = StubProvider([sample_resume_payload] * 10)
    app = _build_app(api_settings, provider)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


class TestHealth:
    async def test_liveness(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_readiness_reports_configured_models(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert "anthropic:claude-opus-5" in body["providers_configured"]

    async def test_readiness_is_503_without_credentials(
        self, sample_resume_payload: dict[str, Any]
    ) -> None:
        settings = Settings(
            llm=LLMSettings(models=[ModelSpec(provider="anthropic", model="m")]),
            cache=CacheSettings(enabled=False),
        )
        app = _build_app(settings, StubProvider([sample_resume_payload]))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"

    async def test_schema_endpoint_publishes_the_contract(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/v1/schema")).json()
        assert body["additionalProperties"] is False
        assert "experience" in body["properties"]

    async def test_openapi_document_is_generated(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/openapi.json")).json()
        assert "/v1/parse" in body["paths"]
        assert "/v1/match" in body["paths"]


class TestParseEndpoint:
    async def test_successful_parse(
        self, client: httpx.AsyncClient, text_resume_bytes: bytes
    ) -> None:
        response = await client.post(
            "/v1/parse", files={"file": ("ada.txt", text_resume_bytes, "text/plain")}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["resume"]["contact"]["email"] == "ada@example.com"
        assert body["resume"]["analytics"]["total_years_of_experience"] > 0
        assert body["document"]["format"] == "txt"
        assert body["usage"]["model"] == "claude-opus-5"

    async def test_response_carries_a_request_id(
        self, client: httpx.AsyncClient, text_resume_bytes: bytes
    ) -> None:
        response = await client.post(
            "/v1/parse", files={"file": ("ada.txt", text_resume_bytes, "text/plain")}
        )
        assert response.headers["X-Request-ID"]
        assert "X-Response-Time-ms" in response.headers

    async def test_upstream_request_id_is_honoured(
        self, client: httpx.AsyncClient, text_resume_bytes: bytes
    ) -> None:
        response = await client.post(
            "/v1/parse",
            files={"file": ("ada.txt", text_resume_bytes, "text/plain")},
            headers={"X-Request-ID": "trace-me"},
        )
        assert response.headers["X-Request-ID"] == "trace-me"

    async def test_unsupported_file_type_is_415(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/parse", files={"file": ("x.bin", bytes(range(256)) * 4, "application/x")}
        )
        assert response.status_code == 415
        body = response.json()
        assert body["code"] == "invalid_document"
        assert body["request_id"]

    async def test_scanned_pdf_gets_a_specific_error(self, client: httpx.AsyncClient) -> None:
        import io

        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buffer = io.BytesIO()
        writer.write(buffer)

        response = await client.post(
            "/v1/parse", files={"file": ("scan.pdf", buffer.getvalue(), "application/pdf")}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "scanned_document"

    async def test_oversized_upload_is_413(
        self, api_settings: Settings, sample_resume_payload: dict[str, Any]
    ) -> None:
        api_settings.extraction.max_file_size = 100
        app = _build_app(api_settings, StubProvider([sample_resume_payload]))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(
                "/v1/parse", files={"file": ("big.txt", b"x" * 5000, "text/plain")}
            )
        assert response.status_code == 413
        assert response.json()["code"] == "document_too_large"

    async def test_missing_file_is_422(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/parse")
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"


class TestBatchEndpoint:
    async def test_mixed_batch_reports_per_document_outcomes(
        self, client: httpx.AsyncClient, text_resume_bytes: bytes
    ) -> None:
        response = await client.post(
            "/v1/parse/batch",
            files=[
                ("files", ("a.txt", text_resume_bytes, "text/plain")),
                ("files", ("bad.txt", b"junk", "text/plain")),
                ("files", ("b.txt", text_resume_bytes, "text/plain")),
            ],
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["succeeded"] == 2
        assert body["failed"] == 1
        failed = next(item for item in body["results"] if item["status"] == "error")
        assert failed["filename"] == "bad.txt"
        assert failed["error_code"] == "empty_document"


class TestMatchEndpoint:
    async def test_scoring_a_parsed_resume(
        self, client: httpx.AsyncClient, text_resume_bytes: bytes
    ) -> None:
        parsed = (
            await client.post(
                "/v1/parse", files={"file": ("ada.txt", text_resume_bytes, "text/plain")}
            )
        ).json()

        response = await client.post(
            "/v1/match",
            json={
                "resume": parsed,
                "requirements": {
                    "required_skills": ["Python", "Kubernetes"],
                    "min_years_experience": 3,
                },
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["match"]["score"] > 0
        assert body["match"]["rationale"]

    async def test_request_without_a_job_is_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/match", json={"resume": None})
        assert response.status_code == 422


class TestAuthentication:
    @pytest.fixture
    def secured_app(self, api_settings: Settings, sample_resume_payload: dict[str, Any]) -> Any:
        api_settings.api_key = SecretStr("s3cret")
        return _build_app(api_settings, StubProvider([sample_resume_payload] * 5))

    async def test_missing_key_is_401(self, secured_app: Any, text_resume_bytes: bytes) -> None:
        transport = httpx.ASGITransport(app=secured_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(
                "/v1/parse", files={"file": ("a.txt", text_resume_bytes, "text/plain")}
            )
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"

    async def test_wrong_key_is_401(self, secured_app: Any, text_resume_bytes: bytes) -> None:
        transport = httpx.ASGITransport(app=secured_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(
                "/v1/parse",
                files={"file": ("a.txt", text_resume_bytes, "text/plain")},
                headers={"x-api-key": "wrong"},
            )
        assert response.status_code == 401

    async def test_correct_key_succeeds(self, secured_app: Any, text_resume_bytes: bytes) -> None:
        transport = httpx.ASGITransport(app=secured_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(
                "/v1/parse",
                files={"file": ("a.txt", text_resume_bytes, "text/plain")},
                headers={"x-api-key": "s3cret"},
            )
        assert response.status_code == 200

    async def test_health_stays_public(self, secured_app: Any) -> None:
        transport = httpx.ASGITransport(app=secured_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            assert (await http.get("/health")).status_code == 200


class TestRateLimiting:
    async def test_limit_is_enforced_and_advertised(
        self, api_settings: Settings, sample_resume_payload: dict[str, Any]
    ) -> None:
        api_settings.server.rate_limit_per_minute = 2
        app = _build_app(api_settings, StubProvider([sample_resume_payload] * 10))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            first = await http.get("/v1/schema")
            second = await http.get("/v1/schema")
            third = await http.get("/v1/schema")

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        assert third.json()["code"] == "rate_limited"
        assert "Retry-After" in third.headers
