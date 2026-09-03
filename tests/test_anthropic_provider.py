"""Tests for the Anthropic provider.

The SDK is faked at the client boundary rather than over HTTP: what matters here is that
we build the right request, read the response correctly, and translate every SDK error
into the domain hierarchy so the retry policy above can act on it.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from resume_parser.exceptions import (
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    ProviderNotConfiguredError,
    StructuredOutputError,
)
from resume_parser.llm.anthropic_provider import AnthropicProvider
from resume_parser.llm.base import StructuredRequest
from resume_parser.settings import ModelSpec

SPEC = ModelSpec(provider="anthropic", model="claude-opus-5")


def _request() -> StructuredRequest:
    return StructuredRequest(
        system="You extract resumes.",
        user="<resume>Ada Lovelace</resume>",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        schema_name="resume_extraction",
        effort="medium",
    )


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Usage:
    def __init__(self) -> None:
        self.input_tokens = 1500
        self.output_tokens = 700
        self.cache_read_input_tokens = 300
        self.cache_creation_input_tokens = 100


class _Message:
    def __init__(
        self, text: str = '{"headline": "Engineer"}', stop_reason: str = "end_turn"
    ) -> None:
        self.content = [_Block(text)]
        self.stop_reason = stop_reason
        self.stop_details = None
        self.model = "claude-opus-5"
        self.usage = _Usage()


class _Messages:
    """Stands in for ``client.messages`` and ``client.beta.messages``."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def create(self, **params: Any) -> Any:
        self.calls.append(params)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Client:
    def __init__(self, result: Any, beta_result: Any = None) -> None:
        self.messages = _Messages(result)
        self.beta = types.SimpleNamespace(
            messages=_Messages(beta_result if beta_result is not None else result)
        )
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _provider(client: _Client, *, refusal_fallback: bool = False) -> AnthropicProvider:
    """Build a provider with its lazily-created SDK client pre-seeded."""
    provider = AnthropicProvider("key", enable_refusal_fallback=refusal_fallback)
    provider._client = client  # type: ignore[assignment]
    return provider


def _sdk_error(error_cls: type[Exception], *, status: int = 429) -> Exception:
    """Construct a real SDK exception.

    The SDK's error classes take genuine ``httpx2`` request/response objects and reach into
    them, so a stub namespace is not enough.
    """
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    try:
        return error_cls(request=request)  # type: ignore[call-arg]
    except TypeError:
        return error_cls(  # type: ignore[call-arg]
            "boom",
            response=httpx2.Response(status, request=request),
            body=None,
        )


class TestRequestConstruction:
    async def test_schema_effort_and_caching_are_sent(self) -> None:
        client = _Client(_Message())
        provider = _provider(client)
        await provider.generate(_request(), SPEC)

        params = client.messages.calls[0]
        assert params["model"] == "claude-opus-5"
        output_config = params["output_config"]
        assert output_config["format"]["type"] == "json_schema"
        assert output_config["format"]["schema"]["additionalProperties"] is False
        assert output_config["effort"] == "medium"
        assert params["thinking"] == {"type": "adaptive"}
        # The system prompt is identical across parses, so it is worth caching.
        assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert params["messages"][0]["content"].startswith("<resume>")


class TestResponseHandling:
    async def test_payload_and_usage_are_extracted(self) -> None:
        provider = _provider(_Client(_Message()))
        response = await provider.generate(_request(), SPEC)

        assert response.data == {"headline": "Engineer"}
        assert response.usage.input_tokens == 1500
        assert response.usage.cache_read_tokens == 300
        assert response.usage.cache_write_tokens == 100
        assert response.usage.total_tokens == 2600

    async def test_truncated_output_is_reported_not_silently_parsed(self) -> None:
        message = _Message(text='{"headline": "Engi', stop_reason="max_tokens")
        provider = _provider(_Client(message))
        with pytest.raises(StructuredOutputError, match="output-token ceiling"):
            await provider.generate(_request(), SPEC)

    async def test_refusal_is_surfaced(self) -> None:
        message = _Message(stop_reason="refusal")
        message.stop_details = types.SimpleNamespace(category="cyber")  # type: ignore[assignment]
        provider = _provider(_Client(message))
        with pytest.raises(LLMError, match="declined"):
            await provider.generate(_request(), SPEC)

    async def test_malformed_json_carries_raw_text_for_the_repair_pass(self) -> None:
        provider = _provider(_Client(_Message(text="Sure! {not json")))
        with pytest.raises(StructuredOutputError) as caught:
            await provider.generate(_request(), SPEC)
        assert caught.value.context["raw_text"] == "Sure! {not json"

    async def test_empty_response_is_rejected(self) -> None:
        provider = _provider(_Client(_Message(text="   ")))
        with pytest.raises(StructuredOutputError, match="empty"):
            await provider.generate(_request(), SPEC)

    async def test_non_object_json_is_rejected(self) -> None:
        provider = _provider(_Client(_Message(text="[1, 2, 3]")))
        with pytest.raises(StructuredOutputError, match="object"):
            await provider.generate(_request(), SPEC)


class TestErrorTranslation:
    @pytest.mark.parametrize(
        ("sdk_error_name", "expected"),
        [
            ("APITimeoutError", LLMTimeoutError),
            ("RateLimitError", LLMRateLimitError),
            ("AuthenticationError", ProviderNotConfiguredError),
            ("APIConnectionError", LLMError),
        ],
    )
    async def test_sdk_errors_map_to_domain_errors(
        self, sdk_error_name: str, expected: type[Exception]
    ) -> None:
        import anthropic

        provider = _provider(_Client(_sdk_error(getattr(anthropic, sdk_error_name))))
        with pytest.raises(expected):
            await provider.generate(_request(), SPEC)

    async def test_server_errors_are_retryable(self) -> None:
        import anthropic

        provider = _provider(_Client(_sdk_error(anthropic.InternalServerError, status=503)))
        with pytest.raises(LLMError, match="server error"):
            await provider.generate(_request(), SPEC)


class TestConfiguration:
    def test_missing_key_is_reported_before_any_call(self) -> None:
        provider = AnthropicProvider(None)
        with pytest.raises(ProviderNotConfiguredError, match="ANTHROPIC_API_KEY"):
            provider._get_client()

    async def test_refusal_fallback_degrades_instead_of_failing(self) -> None:
        """If the beta is not enabled for the account, we drop it rather than 400 forever."""
        import anthropic

        bad_request = _sdk_error(anthropic.BadRequestError, status=400)
        client = _Client(_Message(), beta_result=bad_request)
        provider = _provider(client, refusal_fallback=True)

        response = await provider.generate(_request(), SPEC)
        assert response.data == {"headline": "Engineer"}
        assert len(client.beta.messages.calls) == 1
        assert len(client.messages.calls) == 1
        # The flag is disabled for the rest of the process, so we do not retry the beta.
        await provider.generate(_request(), SPEC)
        assert len(client.beta.messages.calls) == 1

    async def test_close_releases_the_client(self) -> None:
        client = _Client(_Message())
        provider = _provider(client)
        await provider.aclose()
        assert client.closed is True

    def test_missing_sdk_is_reported_clearly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "anthropic", None)
        provider = AnthropicProvider("key")
        with pytest.raises(ProviderNotConfiguredError, match="anthropic"):
            provider._get_client()
