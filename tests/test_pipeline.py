"""End-to-end tests for the parsing service and the result cache."""

from __future__ import annotations

from typing import Any

import pytest

from resume_parser.domain.enums import Proficiency
from resume_parser.exceptions import EmptyDocumentError, StructuredOutputError
from resume_parser.llm.client import LLMClient
from resume_parser.pipeline.cache import ParseCache, build_cache_key
from resume_parser.pipeline.parser import BatchItem, ResumeParsingService
from resume_parser.settings import CacheSettings, Settings
from stubs import StubProvider


class TestParsingService:
    async def test_parse_produces_a_normalized_enriched_result(
        self, service: ResumeParsingService, text_resume_bytes: bytes
    ) -> None:
        result = await service.parse(text_resume_bytes, filename="ada.txt")

        # Normalization ran.
        assert result.resume.contact.email == "ada@example.com"
        assert result.resume.contact.phone == "+14155550142"
        assert result.resume.contact.first_name == "Ada"

        # Deduplication merged the two "python" skills and kept the stronger proficiency.
        names = [skill.name for skill in result.resume.skills]
        assert names.count("Python") == 1
        python = next(s for s in result.resume.skills if s.name == "Python")
        assert python.proficiency is Proficiency.EXPERT
        assert "JavaScript" in names

        # Enrichment ran, and the overlapping consultancy role was not double-counted.
        analytics = result.resume.analytics
        assert analytics.total_years_of_experience < 9.0
        assert analytics.current_company == "Analytical Engines Ltd"
        assert analytics.completeness_score > 0.8

        # Provenance and usage were recorded.
        assert result.document.filename == "ada.txt"
        assert len(result.document.content_sha256) == 64
        assert result.usage.estimated_cost_usd is not None
        assert result.usage.prompt_version

    async def test_dates_are_normalized_to_iso(
        self, service: ResumeParsingService, text_resume_bytes: bytes
    ) -> None:
        result = await service.parse(text_resume_bytes)
        assert result.resume.experience[0].start_date == "2021-01"
        assert result.resume.experience[0].is_current is True

    async def test_prompt_carries_the_resume_text(
        self, service: ResumeParsingService, stub_provider: StubProvider, text_resume_bytes: bytes
    ) -> None:
        await service.parse(text_resume_bytes)
        assert "Ada Lovelace" in stub_provider.calls[0].user
        assert stub_provider.calls[0].schema["additionalProperties"] is False

    async def test_invalid_model_output_is_rejected_not_silently_accepted(
        self, settings: Settings, text_resume_bytes: bytes
    ) -> None:
        provider = StubProvider([{"skills": [{"proficiency": "wizard"}]}])
        service = ResumeParsingService(
            settings, llm=LLMClient(settings, providers={"anthropic": provider})
        )
        with pytest.raises(StructuredOutputError, match="validation"):
            await service.parse(text_resume_bytes)

    async def test_empty_document_never_reaches_the_model(
        self, service: ResumeParsingService, stub_provider: StubProvider
    ) -> None:
        """Failing fast on junk input avoids paying for a guaranteed-bad parse."""
        with pytest.raises(EmptyDocumentError):
            await service.parse(b"tiny")
        assert stub_provider.calls == []

    async def test_low_quality_parse_is_flagged(
        self, settings: Settings, text_resume_bytes: bytes
    ) -> None:
        provider = StubProvider([{"headline": "Someone"}])
        service = ResumeParsingService(
            settings, llm=LLMClient(settings, providers={"anthropic": provider})
        )
        result = await service.parse(text_resume_bytes)
        assert any("completeness" in warning for warning in result.warnings)
        assert any("work experience" in warning for warning in result.warnings)

    async def test_oversized_text_is_truncated_and_reported(
        self, settings: Settings, sample_resume_payload: dict[str, Any]
    ) -> None:
        settings.llm.max_input_characters = 1_000
        provider = StubProvider([sample_resume_payload])
        service = ResumeParsingService(
            settings, llm=LLMClient(settings, providers={"anthropic": provider})
        )
        result = await service.parse(b"x = 1\n" + b"word " * 5000)
        assert result.document.truncated is True
        assert any("truncated" in warning for warning in result.warnings)
        assert len(provider.calls[0].user) < 3_000

    async def test_batch_isolates_failures(
        self, settings: Settings, sample_resume_payload: dict[str, Any], text_resume_bytes: bytes
    ) -> None:
        provider = StubProvider([sample_resume_payload, sample_resume_payload])
        service = ResumeParsingService(
            settings, llm=LLMClient(settings, providers={"anthropic": provider})
        )
        outcomes = await service.parse_batch(
            [
                BatchItem(data=text_resume_bytes, filename="good.txt"),
                BatchItem(data=b"junk", filename="bad.txt"),
                BatchItem(data=text_resume_bytes, filename="also-good.txt"),
            ]
        )
        assert len(outcomes) == 3
        assert isinstance(outcomes[1], EmptyDocumentError)
        assert not isinstance(outcomes[0], Exception)
        assert not isinstance(outcomes[2], Exception)

    async def test_close_releases_providers(
        self, service: ResumeParsingService, stub_provider: StubProvider
    ) -> None:
        await service.aclose()
        assert stub_provider.closed is True


class TestCaching:
    async def test_second_parse_is_served_from_cache(
        self, settings: Settings, sample_resume_payload: dict[str, Any], text_resume_bytes: bytes
    ) -> None:
        settings.cache = CacheSettings(enabled=True)
        provider = StubProvider([sample_resume_payload])
        service = ResumeParsingService(
            settings, llm=LLMClient(settings, providers={"anthropic": provider})
        )
        first = await service.parse(text_resume_bytes, filename="ada.txt")
        second = await service.parse(text_resume_bytes, filename="ada.txt")

        assert len(provider.calls) == 1, "the model should be called only once"
        assert second.usage.cached is True
        assert first.usage.cached is False
        assert second.resume.contact.email == first.resume.contact.email

    async def test_different_documents_do_not_collide(
        self, settings: Settings, sample_resume_payload: dict[str, Any], text_resume_bytes: bytes
    ) -> None:
        settings.cache = CacheSettings(enabled=True)
        provider = StubProvider([sample_resume_payload, sample_resume_payload])
        service = ResumeParsingService(
            settings, llm=LLMClient(settings, providers={"anthropic": provider})
        )
        await service.parse(text_resume_bytes)
        await service.parse(text_resume_bytes + b"\nAdditional section text here.\n")
        assert len(provider.calls) == 2


class TestParseCache:
    async def test_roundtrip(self) -> None:
        cache = ParseCache(CacheSettings(enabled=True))
        await cache.set("k", {"a": 1})
        assert await cache.get("k") == {"a": 1}
        assert cache.stats["hits"] == 1

    async def test_miss_is_counted(self) -> None:
        cache = ParseCache(CacheSettings(enabled=True))
        assert await cache.get("absent") is None
        assert cache.stats["misses"] == 1

    async def test_lru_evicts_the_coldest_entry(self) -> None:
        cache = ParseCache(CacheSettings(enabled=True, max_entries=2))
        await cache.set("a", {"v": 1})
        await cache.set("b", {"v": 2})
        await cache.get("a")  # refresh "a" so "b" becomes coldest
        await cache.set("c", {"v": 3})
        assert await cache.get("b") is None
        assert await cache.get("a") == {"v": 1}

    async def test_ttl_expires_entries(self) -> None:
        cache = ParseCache(CacheSettings(enabled=True, ttl_seconds=1))
        await cache.set("k", {"a": 1})
        cache._entries["k"].stored_at -= 10
        assert await cache.get("k") is None

    async def test_disabled_cache_stores_nothing(self) -> None:
        cache = ParseCache(CacheSettings(enabled=False))
        await cache.set("k", {"a": 1})
        assert await cache.get("k") is None

    async def test_disk_tier_survives_a_new_instance(self, tmp_path: Any) -> None:
        config = CacheSettings(enabled=True, directory=tmp_path / "cache")
        await ParseCache(config).set("k", {"a": 1})
        assert await ParseCache(config).get("k") == {"a": 1}

    def test_key_changes_with_every_input(self) -> None:
        base = {
            "content_sha256": "abc",
            "model_label": "anthropic:claude-opus-5",
            "prompt_version": "2.0.0",
            "schema_fingerprint": "deadbeef",
        }
        baseline = build_cache_key(**base)
        for field in base:
            assert build_cache_key(**{**base, field: "changed"}) != baseline
