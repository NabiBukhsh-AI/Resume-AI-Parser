# Resume AI Parser

Turn a PDF, DOCX or text résumé into a validated, structured record — then score it against a
job description with an explainable, reproducible breakdown.

[![CI](https://github.com/NabiBukhsh-AI/Resume-AI-Parser/actions/workflows/ci.yml/badge.svg)](https://github.com/NabiBukhsh-AI/Resume-AI-Parser/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tests](https://img.shields.io/badge/tests-217%20passing-brightgreen.svg)](tests/)

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
resume-parser parse cv.pdf --summary
```

```
Ada Lovelace
Senior Machine Learning Engineer

  Email        ada@example.com
  Phone        +14155550142
  Location     London, UK
  Experience   7.8 years
  Seniority    senior
  Roles        3
  Skills       14
  Top skills   Python, PyTorch, Kubernetes, SQL, Kafka, AWS
  Completeness 100%
  Model        claude-opus-5
  Cost         $0.0231
  Latency      6820 ms
```

---

## Contents

**Getting started**
[Why this exists](#why-this-exists) ·
[Features](#features) ·
[Install](#install) ·
[Quick start](#quick-start)

**Using it**
[HTTP API](#http-api) ·
[CLI](#cli) ·
[Python library](#python-library) ·
[Web UI](#web-ui) ·
[Configuration](#configuration)

**How it works**
[Architecture](#architecture) ·
[Request lifecycle](#request-lifecycle) ·
[Module breakdown](#module-breakdown) ·
[Data model](#data-model) ·
[LLM layer](#llm-layer) ·
[Job matching](#job-matching)

**Operating it**
[Deployment](#deployment) ·
[Observability](#observability) ·
[Errors](#errors) ·
[Performance and limits](#performance-and-limits) ·
[Privacy and security](#privacy-and-security)

**Working on it**
[Development](#development) ·
[Testing](#testing) ·
[Evaluation](#evaluation) ·
[Extending](#extending) ·
[Migrating from 1.x](#migrating-from-1x) ·
[FAQ](#faq)

---

## Why this exists

Most résumé parsers hand a document to a language model, ask for JSON, and hope. That
approach breaks in four predictable ways, and this project is built around fixing each one.

| The usual failure | What happens | What this does instead |
| --- | --- | --- |
| The schema lives in a prompt | The model drifts from it, and nothing validates the result | One Pydantic model generates the decoding constraint, the validator and the API contract |
| The model does the arithmetic | Overlapping roles get double-counted; the number changes run to run | Date intervals are merged in Python — exact and reproducible |
| Match scores come from a model | The score cannot be audited, tested, or defended | Deterministic weighted scoring with a written rationale per dimension |
| One provider, one attempt | A 429 or a retired model id takes the service down | Retry → model fallback → JSON repair, with typed errors throughout |

Everything else in this README follows from those four decisions.

---

## Features

### Extraction

- **Formats**: PDF (`pypdf`), DOCX (`python-docx`, **including tables** — many résumé
  templates put the entire skills block in an invisible table), TXT and Markdown.
- **Content-based format detection** via `puremagic` plus structural checks. A `.pdf`
  extension on ZIP bytes is rejected rather than fed to the PDF reader.
- **Scanned-PDF detection**: a PDF with no text layer produces a specific
  `scanned_document` error telling you to run OCR — not a confident, empty parse.
- **Pure in-memory processing**. Nothing is ever written to disk, which removes the
  path-traversal surface and the cleanup bugs that come with temp files.
- **Encoding fallback chain** for text files (UTF-8 → UTF-8-BOM → CP1252 → Latin-1).
- **Unicode normalization** (NFKC), so ligatures like `ﬁnance` match `finance` downstream.

### Structured output

- **Strict JSON Schema** generated from the Pydantic model — `additionalProperties: false`,
  every property required, unsupported keywords stripped.
- **Constrained decoding** through the provider's native structured-output mode.
- **Validated on arrival**. A payload that does not satisfy the model is rejected, not
  silently passed through.
- **Tolerant where it should be**: an explicit `null` on a field that has a default is
  coerced to that default rather than failing a whole good extraction.
- **Published contract** at `GET /v1/schema` — generate your own client types from the exact
  schema the model is held to.

### Deterministic enrichment

Computed in Python from the extracted dates, never asked of a model:

- `total_years_of_experience` — **overlapping roles merged and counted once**
- `seniority_level` — inferred from titles, with tenure as the tiebreaker
- `average_tenure_years`, `career_gaps_months`, `companies`
- `top_skills` — ranked by weighted evidence (a skill used in three roles outranks a
  keyword-list entry)
- `completeness_score` and `missing_sections` — the pipeline's own confidence signal

### Normalization

- Dates → ISO 8601 (`YYYY-MM`), from 9+ input formats (`Jan 2021`, `03/2021`, `2021 March`…)
- `Present` / `Current` / `to date` → `is_current: true`, `end_date: null`
- Emails lower-cased and validated; phone numbers stripped to digits and `+`
- Names split or composed so `full_name`, `first_name` and `last_name` are all populated
- **82 skill aliases → 44 canonical names**, so `k8s`, `K8s` and `Kubernetes` are one skill
- Duplicate skills merged, keeping the strongest evidence
- Unparseable values are **preserved verbatim** rather than dropped

### Job matching

- Five weighted dimensions, each explained in the response
- Alias-aware skill matching; skills gathered from the skills list, per-role technologies,
  project stacks and certifications
- Near misses surfaced as `closest_match` rather than silently counted as hits
- Fully deterministic — the same inputs always produce the same score
- Overridable weights per call

### Resilience and cost control

- Retry with **exponential backoff and full jitter** on transient faults
- **Model fallback chain** across providers
- **One JSON repair pass** before giving up
- Non-retryable errors short-circuit instead of burning the budget
- **Content-addressed result cache** (memory LRU + optional disk tier)
- **Prompt caching** on the stable system prefix
- Per-request **token counts, cost estimate, latency, attempt count**

### Interfaces

Three surfaces over **one** `ResumeParsingService`, so behaviour cannot drift:

- **FastAPI** — 7 endpoints, OpenAPI docs, RFC 9457 errors, API-key auth, rate limiting
- **Typer CLI** — parse, batch, match, serve, ui, schema, config
- **Streamlit UI** — upload, tabbed results, inline job matching, JSON download

### Engineering

- **217 tests**, no network and no API key required
- **`mypy --strict`** clean across all 41 modules
- **Ruff** lint + format clean
- **CI** on Ubuntu/Windows/macOS × Python 3.12/3.13, plus wheel and Docker smoke tests
- **Multi-stage Docker** image, non-root, healthchecked
- **Evaluation harness** with baseline comparison for prompt/model changes

---

## Install

Requires **Python 3.12+**.

```bash
git clone https://github.com/NabiBukhsh-AI/Resume-AI-Parser.git
cd Resume-AI-Parser

# With uv (recommended)
uv venv && uv pip install -e ".[ui]"

# Or with pip
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[ui]"
```

| Extra | Adds |
| --- | --- |
| *(none)* | API, CLI, all parsing and matching |
| `[ui]` | Streamlit interface |
| `[dev]` | pytest, mypy, ruff, respx |
| `[all]` | everything |

Set at least one provider credential:

```bash
cp .env.example .env      # then edit it
# or just:
export ANTHROPIC_API_KEY=sk-ant-...
```

Verify what the process actually sees:

```bash
resume-parser config
```

---

## Quick start

```bash
resume-parser parse cv.pdf                        # JSON to stdout
resume-parser parse cv.pdf --summary              # human-readable digest
resume-parser parse cv.pdf -o out.json            # write to a file

resume-parser batch ./resumes -o ./parsed -r      # a directory, in parallel
resume-parser match cv.pdf --job posting.txt      # score against a role

resume-parser serve                               # API  → localhost:8000/docs
resume-parser ui                                  # UI   → localhost:8501
resume-parser schema                              # the JSON Schema
```

---

## HTTP API

```bash
resume-parser serve --port 8000 --workers 4
```

Interactive docs at `/docs`, ReDoc at `/redoc`, OpenAPI at `/openapi.json`.

| Method | Path | Auth | Purpose |
| --- | --- | :---: | --- |
| `POST` | `/v1/parse` | ✅ | Parse one résumé. |
| `POST` | `/v1/parse/batch` | ✅ | Parse many concurrently; per-document errors. |
| `POST` | `/v1/match` | ✅ | Score a parsed résumé against a job. |
| `POST` | `/v1/parse-and-match` | ✅ | Upload résumé + posting; both in one round trip. |
| `GET` | `/v1/schema` | — | The strict JSON Schema used for extraction. |
| `GET` | `/health` | — | Liveness. Never touches a dependency. |
| `GET` | `/health/ready` | — | Readiness. 503 when no model has credentials. |

Auth applies only when `RESUME_PARSER_API_KEY` is set. `/v1/schema` is public by design —
it is the published contract, and `/openapi.json` already exposes the same information.

### `POST /v1/parse`

```bash
curl -X POST http://localhost:8000/v1/parse \
  -H "x-api-key: $RESUME_PARSER_API_KEY" \
  -F "file=@cv.pdf"
```

<details open>
<summary><b>Response</b></summary>

```jsonc
{
  "resume": {
    "contact": {
      "full_name": "Ada Lovelace",
      "first_name": "Ada",
      "last_name": "Lovelace",
      "email": "ada@example.com",          // lower-cased, validated
      "phone": "+14155550142",             // stripped to digits and '+'
      "location": "London, UK",
      "links": { "linkedin": null, "github": "https://github.com/ada",
                 "portfolio": null, "other": [] }
    },
    "headline": "Senior Machine Learning Engineer",
    "summary": "Senior machine learning engineer with experience designing...",
    "experience": [
      {
        "job_title": "Senior Machine Learning Engineer",
        "company": "Analytical Engines Ltd",
        "employment_type": "full_time",
        "location": "London",
        "start_date": "2021-01",           // ISO, from "Jan 2021"
        "end_date": null,                  // null because the role is current
        "is_current": true,
        "description": null,
        "highlights": ["Shipped a ranking model serving 10M requests per day"],
        "technologies": ["Python", "PyTorch", "Kubernetes"]   // canonicalized
      }
    ],
    "education": [
      { "degree": "MSc Computer Science", "field_of_study": "Computer Science",
        "institution": "University of London", "start_date": "2016-01",
        "end_date": "2018-01", "grade": "Distinction", "description": null }
    ],
    "skills": [
      { "name": "Python", "category": "programming_language",
        "proficiency": "expert", "years_of_experience": 8.0 }
    ],
    "certifications": [], "projects": [], "languages": [],
    "awards": [], "publications": [],

    "analytics": {                          // ← computed in Python, not by the model
      "total_years_of_experience": 7.8,     //   overlapping roles counted once
      "seniority_level": "senior",
      "current_position": "Senior Machine Learning Engineer",
      "current_company": "Analytical Engines Ltd",
      "companies": ["Analytical Engines Ltd", "Difference Engine Co"],
      "top_skills": ["Python", "PyTorch", "Kubernetes", "SQL"],
      "average_tenure_years": 2.8,
      "career_gaps_months": 0,
      "completeness_score": 1.0,
      "missing_sections": []
    }
  },

  "document": {
    "filename": "cv.pdf",
    "format": "pdf",
    "size_bytes": 84213,
    "content_sha256": "9f2c...",            // for de-duplication
    "text_characters": 4820,
    "page_count": 2,
    "truncated": false
  },

  "usage": {
    "provider": "anthropic",
    "model": "claude-opus-5",
    "tokens": { "input_tokens": 4210, "output_tokens": 1180,
                "cache_read_tokens": 1024, "cache_write_tokens": 0 },
    "estimated_cost_usd": 0.0231,
    "latency_ms": 6820,
    "attempts": 1,
    "cached": false,
    "fallback_used": false,
    "prompt_version": "2.0.0"
  },

  "warnings": []
}
```

</details>

### `POST /v1/parse/batch`

One failure does not discard the rest — each document reports its own outcome.

```bash
curl -X POST http://localhost:8000/v1/parse/batch \
  -F "files=@a.pdf" -F "files=@b.docx" -F "files=@corrupt.pdf"
```

```jsonc
{
  "total": 3, "succeeded": 2, "failed": 1,
  "results": [
    { "filename": "a.pdf",       "status": "success", "result": { /* ParseResult */ } },
    { "filename": "b.docx",      "status": "success", "result": { /* ParseResult */ } },
    { "filename": "corrupt.pdf", "status": "error",
      "error_code": "scanned_document",
      "error_detail": "This PDF has no extractable text layer..." }
  ]
}
```

### `POST /v1/match`

Pass structured `requirements` to score **for free**, or raw `job_description` to have the
posting structured first with one cheap model call.

```bash
curl -X POST http://localhost:8000/v1/match -H "content-type: application/json" -d '{
  "resume": { /* a previous /v1/parse response, verbatim */ },
  "requirements": {
    "required_skills": ["Python", "Kubernetes", "PyTorch"],
    "preferred_skills": ["Terraform"],
    "min_years_experience": 5,
    "seniority": "senior",
    "education_requirement": "Bachelor'\''s degree"
  }
}'
```

```jsonc
{
  "match": {
    "score": 88.5,
    "breakdown": { "required_skills": 1.0, "preferred_skills": 0.0,
                   "experience": 1.0, "seniority": 1.0, "education": 1.0 },
    "matched_skills": ["Python", "Kubernetes", "PyTorch"],
    "gaps": [{ "skill": "Terraform", "required": false, "closest_match": null }],
    "years_experience": 7.8,
    "meets_experience_bar": true,
    "rationale": [
      "Required skills: 3/3 met.",
      "Preferred skills: 0/1 met.",
      "7.8 years meets the 5-year minimum.",
      "Seniority matches the target level (senior).",
      "Education requirement met."
    ]
  },
  "requirements": { /* echoed back */ }
}
```

---

## CLI

| Command | Options | What it does |
| --- | --- | --- |
| `parse <file>` | `--summary`, `-o`, `-v` | Parse one document. |
| `batch <dir>` | `-o`, `-r`, `-v` | Parse a directory concurrently; one JSON per document. |
| `match <file>` | `--job`, `-v` | Parse and score against a posting. |
| `serve` | `--host`, `--port`, `--reload`, `--workers` | Run the API. |
| `ui` | `--port` | Run the Streamlit interface. |
| `schema` | `-o` | Print the strict JSON Schema. |
| `config` | | Show effective config and usable providers. |

```bash
# Bulk-parse an applicant folder, recursively
resume-parser batch ./applicants -o ./parsed -r

# Generate client types from the published contract
resume-parser schema -o resume.schema.json
npx json-schema-to-typescript resume.schema.json > resume.d.ts
```

`batch` exits non-zero if any document failed, so it drops straight into a pipeline.

---

## Python library

The service is the same object all three surfaces use:

```python
import asyncio
from resume_parser.pipeline.parser import ResumeParsingService
from resume_parser.domain.matching import JobRequirements
from resume_parser.settings import get_settings


async def main() -> None:
    service = ResumeParsingService(get_settings())
    try:
        result = await service.parse(open("cv.pdf", "rb").read(), filename="cv.pdf")

        print(result.resume.contact.full_name)
        print(result.resume.analytics.total_years_of_experience)
        print(f"${result.usage.estimated_cost_usd:.4f}")

        # Deterministic scoring — no model call, no cost
        match = service.match(
            result.resume,
            JobRequirements(required_skills=["Python", "Kubernetes"], min_years_experience=5),
        )
        print(match.score, match.rationale)
    finally:
        await service.aclose()


asyncio.run(main())
```

Bounded-concurrency batch:

```python
from resume_parser.pipeline.parser import BatchItem

outcomes = await service.parse_batch(
    [BatchItem(data=path.read_bytes(), filename=path.name) for path in paths]
)
for outcome in outcomes:
    if isinstance(outcome, Exception):
        print("failed:", outcome)
```

The deterministic pieces are importable on their own — no credentials needed:

```python
from resume_parser.pipeline.enrichment import total_experience_years, merge_intervals
from resume_parser.pipeline.normalization import normalize_date, canonical_skill_name
from resume_parser.pipeline.matching import match_resume_to_job

canonical_skill_name("k8s")  # 'Kubernetes'
normalize_date("Jan 2021")  # '2021-01'
```

---

## Web UI

```bash
resume-parser ui          # → http://localhost:8501
```

Upload a résumé and get tabs for **Overview**, **Experience**, **Skills**, **Job match**,
**JSON** (with download) and **Usage** (model, tokens, cost, latency).

> The 1.x UI showed an "API Key" box and compared what you typed against the server's own
> secret. That protected nothing — anyone who could open the page was already running with
> the server's credentials — while training users to paste secrets into web forms. It is
> gone; access control belongs at the API and the ingress.

---

## Configuration

Precedence, highest first: **environment variables** (and `.env`) → **YAML file** → **defaults**.

Nested keys use a double underscore: `llm.effort` → `RESUME_PARSER_LLM__EFFORT`.

```bash
export RESUME_PARSER_CONFIG_FILE=configs/default.yaml   # optional YAML layer
export RESUME_PARSER_LLM__EFFORT=high
export RESUME_PARSER_SERVER__PORT=9000
```

Credentials are deliberately **not** configurable through YAML — they are read from the
conventional provider variables so a secret never lands in a committable file.

### Reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Anthropic credential. |
| `OPENROUTER_API_KEY` | — | OpenRouter credential. |
| `OPENAI_API_KEY` | — | OpenAI-compatible credential. |
| `RESUME_PARSER_OPENAI_BASE_URL` | `https://api.openai.com/v1` | Point at Ollama, vLLM, LM Studio… |
| `RESUME_PARSER_API_KEY` | unset | `x-api-key` secret. Unset ⇒ the API is open. |
| `RESUME_PARSER_ENVIRONMENT` | `development` | `development` / `staging` / `production`. |
| `RESUME_PARSER_DEBUG` | `false` | Rejected in `production`. |
| **LLM** | | |
| `…LLM__EFFORT` | `medium` | Reasoning depth: `low`…`max`. |
| `…LLM__MAX_OUTPUT_TOKENS` | `16000` | Generation ceiling. |
| `…LLM__MAX_INPUT_CHARACTERS` | `200000` | Résumé text is trimmed to this. |
| `…LLM__TIMEOUT_SECONDS` | `120` | Per-request deadline. |
| `…LLM__MAX_RETRIES` | `3` | Retries per model before falling through. |
| `…LLM__RETRY_BASE_DELAY` | `1.0` | Backoff base, seconds. |
| `…LLM__ENABLE_REPAIR_PASS` | `true` | One self-correction call on malformed JSON. |
| **Extraction** | | |
| `…EXTRACTION__MAX_FILE_SIZE` | `16777216` | 16 MiB upload ceiling. |
| `…EXTRACTION__MIN_TEXT_CHARACTERS` | `120` | Below this ⇒ empty/scanned. |
| `…EXTRACTION__ALLOWED_FORMATS` | `["pdf","docx","txt","md"]` | Accepted formats. |
| **Server** | | |
| `…SERVER__HOST` / `…SERVER__PORT` | `127.0.0.1` / `8000` | Bind address. |
| `…SERVER__ROOT_PATH` | `""` | ASGI root path behind a proxy. |
| `…SERVER__CORS_ORIGINS` | `[]` | Exact origins; empty disables CORS. |
| `…SERVER__MAX_BATCH_SIZE` | `20` | Documents per batch request. |
| `…SERVER__BATCH_CONCURRENCY` | `4` | Parsed in parallel within a batch. |
| `…SERVER__RATE_LIMIT_PER_MINUTE` | `60` | Per client; `0` disables. |
| **Cache** | | |
| `…CACHE__ENABLED` | `true` | Result cache on/off. |
| `…CACHE__MAX_ENTRIES` | `512` | LRU capacity. |
| `…CACHE__TTL_SECONDS` | `86400` | Entry lifetime; `0` = forever. |
| `…CACHE__DIRECTORY` | unset | Enables the disk tier. |
| **Observability** | | |
| `…OBSERVABILITY__LOG_LEVEL` | `INFO` | Root level. |
| `…OBSERVABILITY__JSON_LOGS` | `false` | JSON lines for aggregators. |
| `…OBSERVABILITY__REDACT_PII` | `true` | Scrub PII from logs. |

See [`configs/default.yaml`](configs/default.yaml) for the annotated full set.

---

## Architecture

```
        ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
        │   FastAPI    │   │  Typer CLI   │   │  Streamlit   │      three surfaces,
        │  api/app.py  │   │   cli.py     │   │  ui/*.py     │      zero business logic
        └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
               └──────────────────┼──────────────────┘
                                  ▼
                 ┌────────────────────────────────────┐
                 │       ResumeParsingService         │   the one orchestrator
                 │       pipeline/parser.py           │
                 └────────────────┬───────────────────┘
                                  │
   ┌──────────────┬───────────────┼───────────────┬──────────────────┐
   ▼              ▼               ▼               ▼                  ▼
┌────────┐  ┌──────────┐   ┌────────────┐  ┌────────────┐   ┌───────────────┐
│Extract │→ │ParseCache│→  │ LLMClient  │→ │ Pydantic   │→  │ normalize +   │
│ion     │  │content-  │   │ retry ·    │  │ validate   │   │ enrich        │
│Service │  │addressed │   │ fallback · │  │ or reject  │   │ (pure Python) │
└────────┘  └──────────┘   │ repair     │  └────────────┘   └───────────────┘
   │                       └─────┬──────┘                          │
   │ detect · pypdf              │ Anthropic │ OpenRouter          │ ISO dates
   │ python-docx · text          │ OpenAI-compatible               │ skill aliases
   │ reject scans/junk           ▼                                 │ merge intervals
   │                     strict JSON Schema                        │ seniority
   │                     from the Pydantic model                   │ completeness
   ▼                                                               ▼
 bytes                                                        ParseResult
```

**The invariant:** every arrow moves toward *more validated* data. Bytes become text before
anything trusts them; text becomes a schema-constrained payload; the payload becomes a
validated model before any business logic touches it; and every derived number is computed
after validation, from data that has already been checked.

### Module breakdown

```
src/resume_parser/                    41 modules, mypy --strict clean
│
├── settings.py            Layered config: env → YAML → defaults. Cached, no import-time I/O.
├── exceptions.py          14 typed errors, each carrying its own HTTP status and code.
│
├── domain/                THE SOURCE OF TRUTH
│   ├── resume.py            ResumeExtraction (what the LLM reads) + Resume (+ analytics)
│   ├── matching.py          JobRequirements, MatchResult, MatchBreakdown, SkillGap
│   ├── results.py           ParseResult, DocumentInfo, UsageMetadata, TokenUsage
│   └── enums.py             6 controlled vocabularies → enum constraints in the schema
│
├── extraction/            BYTES → TEXT
│   ├── base.py              TextExtractor protocol + ExtractedText
│   ├── detection.py         Content sniffing; filename is a hint, never authority
│   ├── extractors.py        PDF, DOCX (incl. tables/headers), plain text
│   └── service.py           Size limits, dispatch, empty/scanned checks
│
├── llm/                   TEXT → STRUCTURED JSON
│   ├── base.py              LLMProvider protocol — one method to add a backend
│   ├── schema.py            Pydantic → strict JSON Schema (+ fingerprint for cache keys)
│   ├── prompts.py           Versioned templates; PROMPT_VERSION gates the cache
│   ├── anthropic_provider.py    Official SDK, output_config, prompt caching
│   ├── openai_compatible.py     OpenRouter + OpenAI + Ollama/vLLM/LM Studio
│   └── client.py            Retry · fallback chain · repair pass · cost accounting
│
├── pipeline/              JSON → VALIDATED, ENRICHED RESUME
│   ├── normalization.py     Dates, contacts, 82 skill aliases, dedup
│   ├── enrichment.py        Interval merging, seniority, tenure, gaps, completeness
│   ├── matching.py          Deterministic 5-dimension scoring with rationale
│   ├── cache.py             Content-addressed LRU + optional disk tier
│   └── parser.py            The orchestrator every surface calls
│
├── api/                   HTTP
│   ├── app.py               Factory + lifespan. No module-level app, no import side effects.
│   ├── dependencies.py      Settings/service injection; constant-time API-key check
│   ├── middleware.py        Request IDs, timing, sliding-window rate limiter
│   ├── errors.py            Domain errors → RFC 9457 problem details
│   ├── schemas.py           Wire types that differ from the domain
│   └── routers/             health.py · parse.py
│
├── observability/
│   ├── logging.py           structlog: JSON or console, contextvar correlation
│   └── redaction.py         PII scrubbing applied to every log event
│
├── cli.py                 Typer + Rich
└── ui/streamlit_app.py    View layer only
```

---

## Request lifecycle

What happens on `POST /v1/parse`, in order:

| # | Step | Where | Notes |
| --- | --- | --- | --- |
| 1 | Assign request ID, bind log context | `middleware.py` | Honours an upstream `X-Request-ID`. |
| 2 | Rate-limit check | `middleware.py` | Sliding window, per API key or IP. |
| 3 | Authenticate | `dependencies.py` | `secrets.compare_digest`, only if a key is configured. |
| 4 | Stream upload, enforce size | `routers/parse.py` | Aborts mid-stream — never buffers an oversized file first. |
| 5 | Detect format | `detection.py` | Magic bytes + structure. Extension is a hint only. |
| 6 | Extract text | `extractors.py` | In memory. Nothing touches disk. |
| 7 | Reject empty / scanned | `service.py` | Fails **before** paying for a guaranteed-bad parse. |
| 8 | Trim to input budget | `parser.py` | Head-first — identity and recent roles are highest value. |
| 9 | Cache lookup | `cache.py` | Key = bytes + model + prompt version + schema + input budget. |
| 10 | Call the model | `client.py` | Retry → fallback → repair. Strict schema attached. |
| 11 | Validate | `parser.py` | Pydantic. A bad payload is an error, not a silent pass. |
| 12 | Normalize | `normalization.py` | Dates, contacts, skills. Pure. |
| 13 | Enrich | `enrichment.py` | Interval merge, seniority, completeness. Pure. |
| 14 | Quality warnings | `parser.py` | Low completeness / no experience / no contact details. |
| 15 | Cache, log, respond | | Usage metadata attached; PII redacted in logs. |

Steps 5–8 are the cheap gate: anything that cannot produce a good parse is rejected before a
single token is spent.

---

## Data model

```
Resume
├── contact            full/first/last name · email · phone · location
│   └── links            linkedin · github · portfolio · other[]
├── headline           current or most recent title
├── summary            stated, or written from the document if absent
├── experience[]       job_title · company · employment_type · location
│                      start_date · end_date · is_current
│                      description · highlights[] · technologies[]
├── education[]        degree · field_of_study · institution · location
│                      start_date · end_date · grade · description
├── skills[]           name · category · proficiency · years_of_experience
├── certifications[]   name · issuer · issue_date · expiry_date · credential_id
├── projects[]         name · description · role · url · technologies[] · dates
├── languages[]        name · fluency
├── awards[]  ·  publications[]
│
└── analytics          ← computed in Python, never asked of the model
    ├── total_years_of_experience   overlapping roles merged, counted once
    ├── seniority_level             titles first, tenure as tiebreaker
    ├── current_position · current_company · companies[]
    ├── top_skills[]                ranked by weighted evidence
    ├── average_tenure_years        completed roles only
    ├── career_gaps_months          months no role covers
    ├── completeness_score          0–1, weighted section coverage
    └── missing_sections[]
```

**Controlled vocabularies** (enforced as `enum` in the schema): `Proficiency` (4),
`SkillCategory` (9), `EmploymentType` (8), `SeniorityLevel` (9), `LanguageFluency` (5),
`DocumentFormat` (6).

Fetch the machine-readable contract: `GET /v1/schema` or `resume-parser schema`.

---

## LLM layer

### Providers

| Provider | Transport | Covers |
| --- | --- | --- |
| `anthropic` | Official `anthropic` async SDK | Claude models (default) |
| `openrouter` | `httpx` | Hundreds of models behind one API, including free tiers |
| `openai` | `httpx` | OpenAI, **and** Ollama / vLLM / LM Studio / LiteLLM via `base_url` |

### The model chain

Tried in order; entries whose provider has no credentials are **skipped automatically**, so
you can list several and set only the key you have.

```yaml
llm:
  models:
    - provider: anthropic
      model: claude-opus-5
      input_cost_per_mtok: 5.0
      output_cost_per_mtok: 25.0
    - provider: openrouter            # cheaper fallback
      model: google/gemini-2.5-flash
```

Prices drive `estimated_cost_usd`. Omit them and it reports `null` — a missing number beats
a wrong one in a cost dashboard.

### Resilience

```
                    ┌── transient (429, 5xx, timeout) ──► retry, jittered backoff ──┐
call model ─────────┤                                                                │
                    ├── malformed JSON ────────────────► one repair pass ────────────┤
                    │                                                                │
                    └── hard failure / no credentials ─► next model in the chain ────┘
                                                                                     │
                                     all exhausted ──► AllProvidersFailedError (502) ◄┘
```

Jitter matters under concurrency: without it a batch of parallel parses retries in lockstep
and recreates the burst that caused the rate limit.

### Cost control

- **Prompt caching** on the stable system prefix (identical across every parse).
- **Result cache** keyed on `sha256(bytes) + model + prompt version + schema + input budget`
  — change any of them and stale entries invalidate themselves.
- **`effort: medium`** by default. Extraction is well specified; the top of the range earns
  its cost only on dense or badly formatted documents.
- **`temperature: 0`** on OpenAI-compatible providers — there is one correct answer in the
  document, so sampling variance is pure downside.

---

## Job matching

Five weighted dimensions, each normalized to 0–1 and explained in `rationale`:

| Dimension | Weight | Scoring |
| --- | :---: | --- |
| Required skills | **45%** | Fraction met. Aliases resolve, so `k8s` satisfies `Kubernetes`. |
| Preferred skills | **15%** | Same, on the nice-to-haves. |
| Experience | **20%** | Full marks at or above the bar; proportional credit below it. |
| Seniority | **10%** | Ordinal distance, −25% per level. |
| Education | **10%** | Highest degree held vs. degree requested. |

Weights are overridable per call and re-normalized, so they need not sum to 1.

**A dimension the posting does not state scores 1.0.** It cannot discriminate between
candidates, so it must not penalise them.

**Skill evidence** is gathered from the skills section, per-role `technologies`, project
stacks and certification names — a tool used in a job but missing from the keyword list still
counts. A near miss is reported as `closest_match` rather than silently counted as a hit, so
a human reviewer sees it.

---

## Deployment

```bash
docker compose up --build           # API on :8000
docker compose --profile ui up      # plus the UI on :8501
```

```bash
docker build -t resume-ai-parser .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... resume-ai-parser
```

The image is multi-stage, runs as **non-root** (uid 10001), ships no build tools or source
tree, and its healthcheck hits `/health/ready` — so an instance without working credentials
is kept out of the load balancer instead of serving guaranteed failures.

### Production checklist

- [ ] Set `RESUME_PARSER_API_KEY`, or terminate auth at your gateway.
- [ ] `RESUME_PARSER_ENVIRONMENT=production` (this **rejects** `debug=true`).
- [ ] `…OBSERVABILITY__JSON_LOGS=true`; keep `REDACT_PII=true`.
- [ ] Set `…SERVER__CORS_ORIGINS` to exact origins if a browser calls the API.
- [ ] Probe `/health` for liveness and `/health/ready` for readiness — different questions.
- [ ] **Running >1 instance?** The built-in limiter is **per-process**. Set
      `…RATE_LIMIT_PER_MINUTE=0` and rate-limit at the ingress.
- [ ] Mount a volume for `…CACHE__DIRECTORY` so restarts don't re-pay for parsed documents.
- [ ] Size the LLM timeout below your gateway's, so you return a `504` rather than being cut off.

---

## Observability

Every response carries `X-Request-ID` and `X-Response-Time-ms`. Logs are structlog events,
correlated by request ID via contextvars, JSON or console:

```json
{"event": "parse_complete", "request_id": "af898bba001449c7", "filename": "cv.pdf",
 "model": "claude-opus-5", "latency_ms": 6820, "tokens": 5390,
 "cost_usd": 0.0231, "completeness": 1.0, "level": "info",
 "timestamp": "2026-09-04T10:12:31.882Z"}
```

Key events: `parse_complete` · `parse_cache_hit` · `llm_retry` · `llm_repair_pass` ·
`model_failed` · `model_unavailable` · `request_completed` · `rate_limited` ·
`batch_item_failed`.

`GET /health/ready` exposes cache hit rate, configured models and unavailable models.

---

## Errors

Every failure is [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem details with a
stable `code` and the request ID:

```json
{
  "type": "https://docs.resume-ai-parser.dev/errors/scanned_document",
  "title": "Scanned Document",
  "status": 422,
  "code": "scanned_document",
  "detail": "This PDF has no extractable text layer, which usually means it is a scan or an image export. Re-save it as a text PDF, or run OCR before uploading.",
  "request_id": "af898bba001449c7"
}
```

| Code | Status | Meaning |
| --- | :---: | --- |
| `bad_request` | 400 | Malformed request — e.g. a batch above the size limit. |
| `unauthorized` | 401 | Missing or wrong `x-api-key`. |
| `document_too_large` | 413 | Above `extraction.max_file_size`. |
| `invalid_document` | 415 | Not a supported document type. |
| `empty_document` | 422 | Decoded, but too little text to parse. |
| `scanned_document` | 422 | PDF with no text layer — needs OCR. |
| `extraction_failed` | 422 | Supported format, undecodable bytes. |
| `validation_error` | 422 | Request body did not match the schema. |
| `rate_limited` | 429 | Local budget exhausted. |
| `llm_rate_limited` | 429 | The provider rate-limited us after retries. |
| `internal_error` | 500 | Unexpected. Quote the request ID. |
| `configuration_error` | 500 | The service is mis-configured. |
| `llm_error` | 502 | Generic provider failure. |
| `invalid_structured_output` | 502 | Payload did not satisfy the schema. |
| `all_providers_failed` | 502 | Every model in the chain failed. |
| `provider_not_configured` | 503 | No usable model credentials. |
| `llm_timeout` | 504 | The provider missed its deadline. |

Error bodies never echo your payload back.

---

## Performance and limits

| Aspect | Default | Notes |
| --- | --- | --- |
| Upload ceiling | 16 MiB | Enforced **mid-stream**, not after buffering. |
| Input budget | 200 000 chars | Trimmed head-first; `document.truncated` flags it. |
| Batch size | 20 documents | Batch is read into memory — keep it modest behind auth. |
| Batch concurrency | 4 | Raise with provider rate limits in mind. |
| Rate limit | 60/min/client | **Per process.** Multiply by worker count. |
| LLM timeout | 120 s | Per attempt; wall clock can reach timeout × attempts. |
| Cache | 512 entries, 24 h | Plus an optional disk tier. |
| Typical parse | ~5–10 s, ~5k tokens | 2-page CV. Cache hits are sub-millisecond. |

---

## Privacy and security

Résumés are personal data. The defaults reflect that.

- **Nothing is written to disk.** Uploads are processed entirely in memory. (1.x wrote every
  upload into `uploads/` under the *client-supplied* filename.)
- **Logs are redacted** — emails, phone numbers, URL credentials and document bodies are
  scrubbed by a structlog processor before anything reaches a sink.
- **Error bodies don't echo payloads.**
- **Constant-time key comparison** (`secrets.compare_digest`). 1.x used `!=`, which leaks the
  number of matching leading characters through timing.
- **Filenames are sanitised** to a basename before they are logged or returned.
- **`.gitignore` excludes `*.pdf`/`*.docx`**, and a pre-commit hook blocks them, so candidate
  documents aren't committed by accident.
- **No telemetry.** The only outbound calls are to the LLM provider you configure.

The result cache holds parsed output. If that matters for your retention policy, set
`cache.ttl_seconds`, or turn it off.

See [SECURITY.md](SECURITY.md) for reporting.

---

## Development

```bash
uv pip install -e ".[dev,ui]"
pre-commit install

make check      # lint + types + tests, i.e. everything CI runs
make test       # pytest
make cov        # coverage, with an HTML report
make fmt        # ruff format
make serve      # API with auto-reload
```

| Gate | Command | Status |
| --- | --- | --- |
| Lint | `ruff check .` | clean |
| Format | `ruff format --check .` | clean |
| Types | `mypy` (strict, 41 modules) | clean |
| Tests | `pytest` | **217 passing** |
| Coverage | `pytest --cov` | 78% overall |

CI runs all of it on Ubuntu/Windows/macOS × Python 3.12/3.13, then builds and smoke-tests
both the wheel and the Docker image.

---

## Testing

```bash
pytest                          # everything, ~5s
pytest tests/test_enrichment.py # the date-interval arithmetic
pytest -k "cache or matching"
```

| Suite | Covers |
| --- | --- |
| `test_enrichment.py` | Interval merging, **concurrent roles counted once**, seniority, gaps |
| `test_normalization.py` | Date formats, contacts, skill aliases, dedup |
| `test_extraction.py` | Format detection, PDF/DOCX/text, scanned PDFs, path traversal |
| `test_llm.py` | Strict schema generation, OpenRouter transport, retry/fallback/repair, cost |
| `test_anthropic_provider.py` | Request shape, response handling, SDK error translation |
| `test_pipeline.py` | End-to-end parse, cache semantics, batch isolation |
| `test_api.py` | All endpoints, auth, rate limiting, every error status |
| `test_observability.py` | PII redaction, config layering and precedence |

**The whole suite runs with no network and no API key** — the LLM is replaced by a stub
provider. That is deliberate: tests that hit a real model are slow, flaky and billable. The
parts worth testing continuously are isolated from the one part that cannot be tested cheaply.
For that part, see below.

---

## Evaluation

Unit tests prove the deterministic half is correct. They say nothing about whether the
*model* is extracting well — and that is exactly what regresses when you change a prompt,
switch models, or adjust effort.

```bash
python evals/run_eval.py --output evals/results/baseline.json
# ... change a prompt, swap a model, tune effort ...
python evals/run_eval.py --baseline evals/results/baseline.json
```

Reports field accuracy, company/title/skill recall and precision, numeric error, latency and
cost — and **exits non-zero when quality moves backwards**, so it drops into a scheduled job.
Latency and cost are reported but never fail a run on their own; they move with provider load.

See [`evals/README.md`](evals/README.md).

---

## Extending

### Add an LLM provider

Implement one method:

```python
from resume_parser.llm.base import StructuredRequest, StructuredResponse
from resume_parser.settings import ModelSpec


class MyProvider:
    name = "myprovider"

    async def generate(
        self, request: StructuredRequest, spec: ModelSpec
    ) -> StructuredResponse: ...  # call your backend; return parsed JSON + token usage

    async def aclose(self) -> None: ...
```

Register it in `build_providers()` and add it to the chain. Nothing else changes — the
pipeline, retry policy and all three surfaces are provider-agnostic.

### Add a document format

Implement `TextExtractor` and register it. An OCR backend for scanned PDFs slots in here
without touching `ExtractionService`:

```python
class MyOcrExtractor:
    formats: tuple[DocumentFormat, ...] = (DocumentFormat.PDF,)

    def extract(self, data: bytes) -> ExtractedText: ...


service.register(MyOcrExtractor())
```

### Add a résumé field

Add it to `ResumeExtraction` in `domain/resume.py`. The JSON Schema, the validator, the
OpenAPI contract and the response all pick it up. Bump `PROMPT_VERSION` if you also change
the prompt — that invalidates cached parses automatically.

---

## Migrating from 1.x

Version 2.0 is a full rewrite. **The 1.x tree did not run**: `api.py` and `ui_components.py`
both imported a `file_utils` module that was absent from the repository, and `utils/` had no
`__init__.py`.

| 1.x | 2.0 |
| --- | --- |
| `POST /parse` | `POST /v1/parse` |
| `{talent: {...}}` | `{resume, document, usage, warnings}` |
| `first_name`, `last_name` at top level | under `contact` |
| `total_years_of_experience` (model-generated) | `analytics.total_years_of_experience` (computed) |
| `current_title` | `headline` |
| `streamlit run main.py` | `resume-parser ui` |
| `uvicorn api:app` | `resume-parser serve` |
| `config.yml` (required, CWD-relative) | `configs/default.yaml` (optional) + env vars |

Also fixed along the way:

- `response_format` was `{"type": "json_schema", "schema": …}` — the API expects the schema
  nested under `json_schema` with a `name`, so **the constraint was silently ignored** and
  every response was salvaged with `content.find("{")` (which truncates on a brace inside a
  job description).
- **No request timeout** — a stalled connection hung the worker indefinitely.
- API keys compared with `!=` — timing-attackable.
- Uploads written to disk under the client-supplied filename.
- `python-magic-bin==0.4.14` pinned the project to Windows and broke Linux containers.
- The model list (`gemini-exp-1121`, `gemini-2.0-flash-exp:free`) has since been retired.

---

## FAQ

**Do I have to use Claude?**
No. Any of the three providers works. For local-only operation, point the OpenAI-compatible
provider at Ollama or vLLM with `RESUME_PARSER_OPENAI_BASE_URL`.

**Scanned PDFs?**
Detected and rejected with a `scanned_document` error rather than parsed into nonsense. OCR
is not bundled, but plugs in as a `TextExtractor` — see [Extending](#extending).

**How accurate is `total_years_of_experience`?**
Exact, given correct dates — it is computed by merging date intervals in Python. If it looks
wrong, the *dates* were extracted wrong, which is what the eval harness measures.

**Can I score many candidates against one job cheaply?**
Yes. Structure the posting once into `JobRequirements`, then call `/v1/match` with those
`requirements` for each candidate. Scoring is pure CPU — no model call, no cost.

**Why no module-level `app`?**
So importing `api.app` has no side effects and tests can build an app with their own
settings. Serve it with `uvicorn resume_parser.api.app:create_app --factory`, or just
`resume-parser serve`.

**Is the rate limiter safe behind multiple workers?**
No — it is per-process. Set `…RATE_LIMIT_PER_MINUTE=0` and limit at the ingress.

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Nabi Bukhsh Baloch](https://github.com/NabiBukhsh-AI).
Issues and pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
