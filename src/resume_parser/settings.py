"""Typed, layered application configuration.

Precedence, highest first: constructor arguments, environment variables (and ``.env``),
the YAML file named by ``RESUME_PARSER_CONFIG_FILE``, then the defaults declared here.

Nested values use a double-underscore path, so the YAML key ``llm.temperature`` is set by
``RESUME_PARSER_LLM__TEMPERATURE``. Secrets are read from their conventional
provider-native variables (``ANTHROPIC_API_KEY``, ``OPENROUTER_API_KEY``) so the app drops
into existing deployments without renaming anything.

The old code read a YAML file at import time from a relative path and crashed if the
process happened to start from another directory. Settings here are constructed on demand
and cached, so importing the package never touches the filesystem.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

__all__ = ["Settings", "get_settings", "reload_settings"]

_ENV_PREFIX = "RESUME_PARSER_"
_CONFIG_FILE_ENV = "RESUME_PARSER_CONFIG_FILE"

ProviderName = Literal["anthropic", "openrouter", "openai"]


class ModelSpec(BaseModel):
    """One entry in the model fallback chain."""

    provider: ProviderName = Field(description="Which provider client to route through.")
    model: str = Field(description="Provider-native model identifier.")
    input_cost_per_mtok: float | None = Field(
        default=None, ge=0, description="USD per million input tokens, for cost estimates."
    )
    output_cost_per_mtok: float | None = Field(
        default=None, ge=0, description="USD per million output tokens, for cost estimates."
    )

    @property
    def label(self) -> str:
        """Human-readable ``provider:model`` identifier used in logs and metadata."""
        return f"{self.provider}:{self.model}"


class LLMSettings(BaseModel):
    """How we talk to language models."""

    models: list[ModelSpec] = Field(
        default_factory=lambda: [
            ModelSpec(
                provider="anthropic",
                model="claude-opus-5",
                input_cost_per_mtok=5.0,
                output_cost_per_mtok=25.0,
            ),
        ],
        description=(
            "Ordered fallback chain. The first entry is tried first; each subsequent entry "
            "is tried only if the ones before it fail."
        ),
    )
    max_output_tokens: int = Field(
        default=16_000,
        ge=1024,
        le=128_000,
        description="Ceiling on generated tokens. Resume JSON rarely exceeds a few thousand.",
    )
    effort: Literal["low", "medium", "high", "xhigh", "max"] = Field(
        default="medium",
        description=(
            "Reasoning effort for models that support it. Extraction is a well-specified "
            "task, so 'medium' is the cost/quality sweet spot; raise it for messy scans."
        ),
    )
    max_input_characters: int = Field(
        default=200_000,
        ge=1_000,
        description="Resume text is trimmed to this length before being sent to a model.",
    )
    timeout_seconds: float = Field(
        default=120.0, gt=0, description="Per-request deadline for a provider call."
    )
    max_retries: int = Field(
        default=3, ge=0, le=10, description="Retries per model before falling through the chain."
    )
    retry_base_delay: float = Field(
        default=1.0, gt=0, description="Base seconds for exponential backoff between retries."
    )
    enable_repair_pass: bool = Field(
        default=True,
        description=(
            "When a provider returns malformed JSON, send the payload back once asking for "
            "a corrected version instead of failing the request outright."
        ),
    )


class CacheSettings(BaseModel):
    """Content-addressed result caching.

    Parsing the same document twice is pure waste: the input is immutable and the model is
    pinned, so the result is a pure function of (bytes, model, prompt version, schema).
    """

    enabled: bool = Field(default=True, description="Turn the parse cache on or off.")
    max_entries: int = Field(default=512, ge=1, description="LRU capacity, in entries.")
    ttl_seconds: int = Field(default=86_400, ge=0, description="Entry lifetime; 0 means forever.")
    directory: Path | None = Field(
        default=None,
        description="Optional directory for a disk tier that survives restarts.",
    )


class ExtractionSettings(BaseModel):
    """Limits and heuristics for turning bytes into text."""

    max_file_size: int = Field(
        default=16 * 1024 * 1024, ge=1, description="Largest accepted upload, in bytes."
    )
    min_text_characters: int = Field(
        default=120,
        ge=0,
        description=(
            "Below this, a document is treated as empty. For PDFs it usually means a scan "
            "with no text layer, which we report as needing OCR."
        ),
    )
    allowed_formats: list[str] = Field(
        default_factory=lambda: ["pdf", "docx", "txt", "md"],
        description="Document formats the service will accept.",
    )


class ServerSettings(BaseModel):
    """HTTP surface configuration."""

    host: str = Field(default="127.0.0.1", description="Bind address.")
    port: int = Field(default=8000, ge=1, le=65_535, description="Bind port.")
    root_path: str = Field(default="", description="ASGI root path when behind a proxy.")
    cors_origins: list[str] = Field(
        default_factory=list, description="Exact origins allowed by CORS; empty disables CORS."
    )
    max_batch_size: int = Field(
        default=20, ge=1, le=200, description="Documents accepted per batch request."
    )
    batch_concurrency: int = Field(
        default=4, ge=1, le=64, description="Documents parsed in parallel within one batch."
    )
    rate_limit_per_minute: int = Field(
        default=60, ge=0, description="Requests per client per minute; 0 disables the limiter."
    )
    request_timeout_seconds: float = Field(
        default=180.0, gt=0, description="Server-side ceiling on a single parse request."
    )


class ObservabilitySettings(BaseModel):
    """Logging behaviour."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Root log level."
    )
    json_logs: bool = Field(
        default=False, description="Emit JSON lines instead of human-readable console output."
    )
    redact_pii: bool = Field(
        default=True,
        description=(
            "Scrub emails, phone numbers and long text bodies from log records. Resumes are "
            "personal data; leaving this off risks writing PII into log storage."
        ),
    )


class Settings(BaseSettings):
    """Root configuration object."""

    model_config = SettingsConfigDict(
        env_prefix=_ENV_PREFIX,
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        secrets_dir=None,
    )

    app_name: str = Field(default="Resume AI Parser", description="Name shown in API docs.")
    environment: Literal["development", "staging", "production"] = Field(
        default="development", description="Deployment environment."
    )
    debug: bool = Field(default=False, description="Verbose errors; never enable in production.")

    api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Shared secret required on protected routes. When unset, the API is open - "
            "which is fine locally and a mistake in production."
        ),
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Credential for the Anthropic provider.",
    )
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENROUTER_API_KEY",
        description="Credential for the OpenRouter provider.",
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
        description="Credential for any OpenAI-compatible endpoint.",
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Base URL for the OpenAI-compatible provider (Ollama, vLLM, LM Studio, ...).",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    @field_validator("llm")
    @classmethod
    def _require_at_least_one_model(cls, value: LLMSettings) -> LLMSettings:
        if not value.models:
            msg = "llm.models must list at least one model"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _warn_on_open_production(self) -> Settings:
        if self.environment == "production" and self.debug:
            msg = "debug must be disabled in production"
            raise ValueError(msg)
        return self

    # ---------------------------------------------------------------- convenience

    def secret_for(self, provider: str) -> str | None:
        """Return the plaintext credential for ``provider``, or ``None`` if unset."""
        secret = {
            "anthropic": self.anthropic_api_key,
            "openrouter": self.openrouter_api_key,
            "openai": self.openai_api_key,
        }.get(provider)
        return secret.get_secret_value() if secret else None

    def configured_models(self) -> list[ModelSpec]:
        """The fallback chain filtered down to providers that actually have credentials."""
        return [spec for spec in self.llm.models if self.secret_for(spec.provider)]

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the YAML layer beneath the environment layers."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _YamlSettingsSource(settings_cls),
            file_secret_settings,
        )


class _YamlSettingsSource(PydanticBaseSettingsSource):
    """Loads defaults from the YAML file named by ``RESUME_PARSER_CONFIG_FILE``.

    Absent or empty files are not an error - YAML is an optional convenience layer, and
    the application is fully configurable through the environment alone.
    """

    def __call__(self) -> dict[str, Any]:
        import os

        raw_path = os.getenv(_CONFIG_FILE_ENV)
        if not raw_path:
            return {}
        path = Path(raw_path).expanduser()
        if not path.is_file():
            msg = f"{_CONFIG_FILE_ENV} points at {path}, which does not exist"
            raise FileNotFoundError(msg)
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            return {}
        if not isinstance(loaded, dict):
            msg = f"{path} must contain a YAML mapping at the top level"
            raise TypeError(msg)
        return loaded

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        """Unused: this source supplies the whole mapping in :meth:`__call__`."""
        return None, field_name, False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that configuration is parsed once. Use it as a FastAPI dependency rather
    than importing a module-level constant, which keeps tests able to override it.
    """
    return Settings()


def reload_settings() -> Settings:
    """Drop the cached settings and rebuild them. Intended for tests and CLI overrides."""
    get_settings.cache_clear()
    return get_settings()


# Re-exported for callers that want to type a dependency without importing pydantic.
SettingsProvider = Callable[[], Settings]
