"""Tests for PII redaction and configuration loading.

Redaction is a privacy control, not a nicety: resumes are personal data, and a log
aggregator is one of the easiest places to leak it from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_parser.observability.redaction import redact_mapping, redact_text, truncate
from resume_parser.settings import Settings, reload_settings


class TestRedactText:
    def test_email_is_masked(self) -> None:
        assert redact_text("write to ada@example.com today") == "write to [email] today"

    @pytest.mark.parametrize(
        "phone",
        ["+1 (415) 555-0142", "+44 20 7946 0958", "415-555-0142", "00 44 7700 900123"],
    )
    def test_phone_shapes_are_masked(self, phone: str) -> None:
        assert phone not in redact_text(f"call {phone} now")

    def test_url_credentials_are_masked(self) -> None:
        masked = redact_text("postgres://user:hunter2@db.internal/app")
        assert "hunter2" not in masked

    def test_ordinary_text_is_untouched(self) -> None:
        assert redact_text("Senior Engineer at Acme") == "Senior Engineer at Acme"


class TestTruncate:
    def test_short_text_passes_through(self) -> None:
        assert truncate("short", 100) == "short"

    def test_long_text_reports_what_was_dropped(self) -> None:
        result = truncate("x" * 200, 50)
        assert result.startswith("x" * 50)
        assert "+150 chars" in result


class TestRedactMapping:
    def test_secret_keys_are_masked_entirely(self) -> None:
        redacted = redact_mapping(
            {"api_key": "sk-ant-real-secret", "Authorization": "Bearer abc", "model": "opus"}
        )
        assert redacted["api_key"] == "[redacted]"
        assert redacted["Authorization"] == "[redacted]"
        assert redacted["model"] == "opus"

    def test_document_bodies_are_truncated_and_scrubbed(self) -> None:
        redacted = redact_mapping({"text": "ada@example.com " + "word " * 200}, text_limit=40)
        assert "ada@example.com" not in redacted["text"]
        assert "chars]" in redacted["text"]

    def test_nested_structures_are_walked(self) -> None:
        redacted = redact_mapping(
            {"outer": {"contact": "ada@example.com", "token": "abc"}, "items": ["x@y.com"]}
        )
        assert redacted["outer"]["contact"] == "[email]"
        assert redacted["outer"]["token"] == "[redacted]"
        assert redacted["items"] == ["[email]"]

    def test_non_string_values_survive(self) -> None:
        redacted = redact_mapping({"count": 42, "ok": True, "ratio": 0.5, "none": None})
        assert redacted == {"count": 42, "ok": True, "ratio": 0.5, "none": None}


class TestSettings:
    def test_defaults_are_usable(self) -> None:
        settings = Settings()
        assert settings.llm.models
        assert settings.llm.models[0].provider == "anthropic"
        assert "pdf" in settings.extraction.allowed_formats

    def test_configured_models_filters_on_credentials(self) -> None:
        settings = Settings(anthropic_api_key="k")
        assert [spec.label for spec in settings.configured_models()] == ["anthropic:claude-opus-5"]

    def test_models_without_credentials_are_excluded(self) -> None:
        assert Settings().configured_models() == []

    def test_secret_lookup_by_provider(self) -> None:
        settings = Settings(openrouter_api_key="or-key")
        assert settings.secret_for("openrouter") == "or-key"
        assert settings.secret_for("anthropic") is None

    def test_debug_is_rejected_in_production(self) -> None:
        with pytest.raises(ValueError, match="debug"):
            Settings(environment="production", debug=True)

    def test_empty_model_chain_is_rejected(self) -> None:
        from resume_parser.settings import LLMSettings

        with pytest.raises(ValueError, match="at least one model"):
            Settings(llm=LLMSettings(models=[]))

    def test_yaml_layer_is_loaded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = tmp_path / "config.yaml"
        config.write_text(
            "app_name: Custom Parser\n"
            "llm:\n"
            "  effort: high\n"
            "  max_output_tokens: 8000\n"
            "extraction:\n"
            "  allowed_formats: [pdf]\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("RESUME_PARSER_CONFIG_FILE", str(config))
        settings = reload_settings()
        try:
            assert settings.app_name == "Custom Parser"
            assert settings.llm.effort == "high"
            assert settings.llm.max_output_tokens == 8000
            assert settings.extraction.allowed_formats == ["pdf"]
        finally:
            monkeypatch.delenv("RESUME_PARSER_CONFIG_FILE")
            reload_settings()

    def test_environment_overrides_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("app_name: From YAML\n", encoding="utf-8")
        monkeypatch.setenv("RESUME_PARSER_CONFIG_FILE", str(config))
        monkeypatch.setenv("RESUME_PARSER_APP_NAME", "From Env")
        try:
            assert reload_settings().app_name == "From Env"
        finally:
            monkeypatch.delenv("RESUME_PARSER_CONFIG_FILE")
            monkeypatch.delenv("RESUME_PARSER_APP_NAME")
            reload_settings()

    def test_missing_config_file_fails_loudly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo in the config path must not silently fall back to defaults."""
        monkeypatch.setenv("RESUME_PARSER_CONFIG_FILE", str(tmp_path / "absent.yaml"))
        try:
            with pytest.raises(FileNotFoundError):
                reload_settings()
        finally:
            monkeypatch.delenv("RESUME_PARSER_CONFIG_FILE")
            reload_settings()

    def test_nested_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RESUME_PARSER_LLM__EFFORT", "xhigh")
        try:
            assert reload_settings().llm.effort == "xhigh"
        finally:
            monkeypatch.delenv("RESUME_PARSER_LLM__EFFORT")
            reload_settings()
