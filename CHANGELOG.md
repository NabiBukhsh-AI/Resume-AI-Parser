# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.1] - 2026-09-04

### Fixed

- **Client mistakes no longer return 500.** An oversized batch and a `/v1/match` request
  without a `resume` were both raised as a bare `ResumeParserError`, whose default status is
  500 - blaming the server for the caller's error and hiding the real problem. Added
  `BadRequestError` (400) for the batch limit, and made `MatchRequest.resume` required so a
  missing résumé is rejected as a 422 before the handler runs.
- **Stale cache entries after a config change.** The cache key omitted
  `llm.max_input_characters`, which decides where a long résumé is truncated and therefore
  which sections the model ever saw. Changing that limit now invalidates affected entries.

### Changed

- README rewritten with the full architecture, module breakdown, request lifecycle, complete
  configuration reference, error table and extension guide.

## [2.0.0] - 2026-09-03

A complete rewrite. Version 1.x did not run: `api.py` and `ui_components.py` both imported a
`file_utils` module that was never committed, and `utils/` had no `__init__.py`.

### Added

- **Deterministic analytics.** Total experience, seniority, average tenure, career gaps and a
  completeness score, computed in Python from the extracted dates. Overlapping roles are
  merged so concurrent work is counted once.
- **Job matching.** `POST /v1/match` and `POST /v1/parse-and-match` score a résumé against a
  posting across five weighted dimensions, returning matched skills, gaps and a rationale.
  Scoring is deterministic and needs no model call when requirements are supplied structured.
- **Provider abstraction.** Anthropic (default), OpenRouter, and any OpenAI-compatible
  endpoint including Ollama, vLLM and LM Studio. Adding a backend means implementing one method.
- **Resilience.** Retry with jittered exponential backoff, fallback down a model chain, and a
  single JSON repair pass before failing.
- **Content-addressed cache** keyed on document bytes, model, prompt version and schema, with
  an optional disk tier.
- **Batch parsing** with bounded concurrency; per-document failures never fail the request.
- **A real CLI** (`parse`, `batch`, `match`, `serve`, `ui`, `schema`, `config`).
- **Structured logging** via structlog, with request-ID correlation and PII redaction on by default.
- **Usage metadata** on every response: model, tokens, estimated cost, latency, attempts,
  cache and fallback status.
- **Split health probes** — `/health` for liveness, `/health/ready` for readiness.
- **`GET /v1/schema`** publishing the exact JSON Schema the model is constrained to.
- **Richer domain model** — certifications, projects, languages, awards, publications,
  per-role highlights and technologies, employment type, and web presence links.
- **217 tests**, `mypy --strict`, Ruff, multi-platform CI, a multi-stage Dockerfile, and an
  evaluation harness with regression detection.

### Changed

- **Schema is derived from Pydantic models**, not hand-written YAML, and used for constrained
  decoding, validation, the OpenAPI contract and the internal types.
- **Documents are processed in memory.** Uploads are no longer written to an `uploads/`
  directory under the client-supplied filename.
- **Configuration** moved to `pydantic-settings` with env → YAML → defaults layering,
  constructed on demand instead of read from a CWD-relative path at import time.
- **Errors** are a typed hierarchy mapped to RFC 9457 problem responses, replacing
  `except ValueError -> 400 / except Exception -> 500`.
- **Format detection** reads magic bytes with `puremagic` rather than trusting the extension.
- **API surface versioned** under `/v1`; responses are wrapped in a result envelope.
- Minimum Python is now **3.12**.

### Fixed

- **The package now imports.** The missing `file_utils` module is gone; its behaviour lives in
  `resume_parser.extraction`.
- **`response_format` was malformed** — the schema was sent at the wrong nesting level, so the
  constraint was ignored and every response was salvaged with `content.find("{")`. That brace
  scan also truncated on any `{` inside a string value.
- **No request timeout** on the provider call, so a stalled connection hung the worker forever.
- **Timing-attackable auth** — `x-api-key` was compared with `!=`; now `secrets.compare_digest`.
- **Blocking I/O in an async endpoint** — `requests` inside `async def` stalled the event loop.
- **Path traversal surface** — the client-supplied filename was used to build a disk path.
- **`python-magic-bin==0.4.14`** made the project unbuildable outside Windows.
- **Scanned PDFs** returned an empty parse; they now raise a specific, actionable error.
- **Deprecated model IDs** in the default configuration.

### Removed

- The API-key box in the Streamlit UI, which compared user input against the server's own
  secret. It protected nothing and taught users to paste shared secrets into a web form.
- `uploads/` and all temporary-file handling.

[2.0.0]: https://github.com/NabiBukhsh-AI/Resume-AI-Parser/releases/tag/v2.0.0
