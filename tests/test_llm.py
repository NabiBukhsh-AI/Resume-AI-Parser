"""Tests for schema generation, provider transport and the resilience layer."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from resume_parser.domain.matching import JobRequirements
from resume_parser.domain.results import TokenUsage
from resume_parser.domain.resume import ResumeExtraction
from resume_parser.exceptions import (
    AllProvidersFailedError,
    LLMError,
    LLMRateLimitError,
    ProviderNotConfiguredError,
    StructuredOutputError,
)
from resume_parser.llm.base import StructuredRequest
from resume_parser.llm.client import LLMClient, estimate_cost
from resume_parser.llm.openai_compatible import OpenRouterProvider, extract_json_object
from resume_parser.llm.schema import schema_fingerprint, to_strict_json_schema
from resume_parser.settings import LLMSettings, ModelSpec, Settings
from stubs import StubProvider


def _request() -> StructuredRequest:
    return StructuredRequest(system="sys", user="usr", schema={"type": "object"})


class TestStrictSchema:
    def test_every_object_forbids_extra_properties(self) -> None:
        schema = to_strict_json_schema(ResumeExtraction)

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object" or "properties" in node:
                    assert node.get("additionalProperties") is False
                    assert set(node["required"]) == set(node.get("properties", {}))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(schema)

    def test_unsupported_keywords_are_stripped(self) -> None:
        schema = to_strict_json_schema(ResumeExtraction)
        rendered = repr(schema)
        for keyword in ('"default"', '"maximum"', '"minimum"', '"format"'):
            assert keyword not in rendered

    def test_optional_fields_stay_nullable(self) -> None:
        schema = to_strict_json_schema(ResumeExtraction)
        headline = schema["properties"]["headline"]
        assert {"type": "null"} in headline["anyOf"]

    def test_required_object_fields_are_not_made_nullable(self) -> None:
        """`contact` has a default factory; a null there would fail validation."""
        schema = to_strict_json_schema(ResumeExtraction)
        contact = schema["properties"]["contact"]
        assert "anyOf" not in contact
        assert contact["$ref"].endswith("ContactInfo")

    def test_lists_are_never_nullable(self) -> None:
        schema = to_strict_json_schema(ResumeExtraction)
        assert schema["properties"]["skills"]["type"] == "array"

    def test_fingerprint_is_stable_and_sensitive(self) -> None:
        first = schema_fingerprint(to_strict_json_schema(ResumeExtraction))
        assert first == schema_fingerprint(to_strict_json_schema(ResumeExtraction))
        assert first != schema_fingerprint(to_strict_json_schema(JobRequirements))


class TestJsonRecovery:
    def test_plain_object(self) -> None:
        assert extract_json_object('{"a": 1}') == {"a": 1}

    def test_fenced_block(self) -> None:
        assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_object_with_a_prose_preamble(self) -> None:
        assert extract_json_object('Here you go:\n{"a": 1}\nHope that helps.') == {"a": 1}

    def test_braces_inside_strings_do_not_truncate(self) -> None:
        """The original `find('{')`/`rfind('}')` approach broke on exactly this input."""
        payload = '{"summary": "Managed the {growth} team", "n": 2}'
        assert extract_json_object(payload)["n"] == 2

    def test_non_object_is_rejected(self) -> None:
        with pytest.raises(StructuredOutputError):
            extract_json_object("just some prose")


class TestOpenRouterProvider:
    @respx.mock
    async def test_successful_call_parses_payload_and_usage(self) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "google/gemini-2.5-flash",
                    "choices": [{"message": {"content": '{"headline": "Engineer"}'}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                },
            )
        )
        provider = OpenRouterProvider("key")
        response = await provider.generate(
            _request(), ModelSpec(provider="openrouter", model="google/gemini-2.5-flash")
        )
        assert response.data == {"headline": "Engineer"}
        assert response.usage.input_tokens == 100
        await provider.aclose()

    @respx.mock
    async def test_response_format_is_nested_correctly(self) -> None:
        """The original sent `{"type": ..., "schema": ...}`, which the API ignores."""
        route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
        )
        provider = OpenRouterProvider("key")
        await provider.generate(_request(), ModelSpec(provider="openrouter", model="m"))

        body = json.loads(respx.calls.last.request.content)
        response_format = body["response_format"]
        assert response_format["type"] == "json_schema"
        # The schema must sit under a `json_schema` key, not directly under `schema`.
        assert response_format["json_schema"]["strict"] is True
        assert response_format["json_schema"]["schema"] == {"type": "object"}
        assert body["temperature"] == 0
        assert route.called
        await provider.aclose()

    @respx.mock
    async def test_rate_limit_maps_to_a_retryable_error(self) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(429, text="slow down")
        )
        provider = OpenRouterProvider("key")
        with pytest.raises(LLMRateLimitError):
            await provider.generate(_request(), ModelSpec(provider="openrouter", model="m"))
        await provider.aclose()

    @respx.mock
    async def test_auth_failure_is_not_retryable(self) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(401, text="bad key")
        )
        provider = OpenRouterProvider("key")
        with pytest.raises(ProviderNotConfiguredError):
            await provider.generate(_request(), ModelSpec(provider="openrouter", model="m"))
        await provider.aclose()

    @respx.mock
    async def test_error_inside_a_200_body_is_surfaced(self) -> None:
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"error": {"message": "upstream down"}})
        )
        provider = OpenRouterProvider("key")
        with pytest.raises(LLMError, match="upstream down"):
            await provider.generate(_request(), ModelSpec(provider="openrouter", model="m"))
        await provider.aclose()

    def test_missing_key_is_reported_clearly(self) -> None:
        provider = OpenRouterProvider(None)
        with pytest.raises(ProviderNotConfiguredError):
            provider._get_client()


class TestResilience:
    def _settings(self, **overrides: Any) -> Settings:
        return Settings(
            anthropic_api_key="k",
            openrouter_api_key="k2",
            llm=LLMSettings(
                models=[
                    ModelSpec(provider="anthropic", model="primary"),
                    ModelSpec(provider="openrouter", model="backup"),
                ],
                max_retries=2,
                retry_base_delay=0.001,
                **overrides,
            ),
        )

    async def test_retries_a_transient_failure_then_succeeds(self) -> None:
        provider = StubProvider([{"headline": "ok"}])
        calls = {"n": 0}
        original = provider.generate

        async def flaky(request: Any, spec: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise LLMRateLimitError("429")
            return await original(request, spec)

        provider.generate = flaky  # type: ignore[method-assign]
        client = LLMClient(self._settings(), providers={"anthropic": provider})
        data, usage = await client.generate(_request())
        assert data == {"headline": "ok"}
        assert usage.attempts == 2

    async def test_falls_through_to_the_next_model(self) -> None:
        primary = StubProvider()
        primary.error = LLMError("model retired")
        backup = StubProvider([{"headline": "from backup"}])
        backup.name = "openrouter"

        client = LLMClient(self._settings(), providers={"anthropic": primary, "openrouter": backup})
        data, usage = await client.generate(_request())
        assert data == {"headline": "from backup"}
        assert usage.fallback_used is True
        assert usage.model == "backup"

    async def test_every_model_failing_raises_with_a_summary(self) -> None:
        primary = StubProvider()
        primary.error = LLMError("down")
        backup = StubProvider()
        backup.error = LLMError("also down")

        client = LLMClient(self._settings(), providers={"anthropic": primary, "openrouter": backup})
        with pytest.raises(AllProvidersFailedError) as caught:
            await client.generate(_request())
        assert len(caught.value.context["failures"]) == 2

    async def test_no_credentials_is_a_clear_configuration_error(self) -> None:
        settings = Settings(llm=LLMSettings(models=[ModelSpec(provider="anthropic", model="m")]))
        client = LLMClient(settings, providers={"anthropic": StubProvider()})
        with pytest.raises(ProviderNotConfiguredError, match="ANTHROPIC_API_KEY"):
            await client.generate(_request())

    async def test_malformed_json_triggers_one_repair_pass(self) -> None:
        provider = StubProvider([{"headline": "repaired"}])
        state = {"first": True}
        original = provider.generate

        async def broken_then_fixed(request: Any, spec: Any) -> Any:
            if state["first"]:
                state["first"] = False
                raise StructuredOutputError("bad json", raw_text="{oops")
            return await original(request, spec)

        provider.generate = broken_then_fixed  # type: ignore[method-assign]
        client = LLMClient(self._settings(), providers={"anthropic": provider})
        data, _ = await client.generate(_request())
        assert data == {"headline": "repaired"}


class TestCostEstimation:
    def test_cost_is_computed_from_the_price_table(self) -> None:
        spec = ModelSpec(
            provider="anthropic",
            model="m",
            input_cost_per_mtok=5.0,
            output_cost_per_mtok=25.0,
        )
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
        assert estimate_cost(usage, spec) == pytest.approx(30.0)

    def test_cache_reads_are_discounted(self) -> None:
        spec = ModelSpec(
            provider="anthropic", model="m", input_cost_per_mtok=10.0, output_cost_per_mtok=0.0
        )
        usage = TokenUsage(cache_read_tokens=1_000_000)
        assert estimate_cost(usage, spec) == pytest.approx(1.0)

    def test_unknown_prices_return_none_rather_than_zero(self) -> None:
        spec = ModelSpec(provider="anthropic", model="m")
        assert estimate_cost(TokenUsage(input_tokens=1000), spec) is None
