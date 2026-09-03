# Resume AI Parser

Turn a PDF, DOCX or text résumé into a validated, structured record — then score it against
a job description with an explainable breakdown.

[![CI](https://github.com/NabiBukhsh-AI/Resume-AI-Parser/actions/workflows/ci.yml/badge.svg)](https://github.com/NabiBukhsh-AI/Resume-AI-Parser/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
resume-parser parse cv.pdf --summary
```

```
Ada Lovelace
Senior Machine Learning Engineer

  Email        ada@example.com
  Experience   7.8 years
  Seniority    senior
  Top skills   Python, PyTorch, Kubernetes, SQL, Kafka, AWS
  Completeness 100%
  Model        claude-opus-5
  Cost         $0.0231
  Latency      6820 ms
```

---

## Contents

- [What it does](#what-it-does)
- [Design decisions worth knowing](#design-decisions-worth-knowing)
- [Install](#install)
- [Quick start](#quick-start)
- [The HTTP API](#the-http-api)
- [The CLI](#the-cli)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [The data model](#the-data-model)
- [Job matching](#job-matching)
- [Deployment](#deployment)
- [Development](#development)
- [Evaluation](#evaluation)
- [Privacy](#privacy)
- [Migrating from 1.x](#migrating-from-1x)
- [License](#license)

---

## What it does

- **Extracts** contact details, work history, education, skills, certifications, projects,
  languages, awards and publications — as a Pydantic-validated object, not a hopeful dict.
- **Computes** total experience, seniority, tenure, career gaps and a completeness score in
  Python, from the extracted dates. No arithmetic is delegated to a language model.
- **Normalizes** dates to ISO 8601, emails and phone numbers to canonical form, and skill
  names through an alias table, so `k8s`, `K8s` and `Kubernetes` are one skill.
- **Matches** a résumé against a job description and returns a 0–100 score with per-dimension
  sub-scores, matched skills, missing requirements and a written rationale.
- **Serves** all of it three ways — a FastAPI service, a Typer CLI, and a Streamlit UI — that
  share one code path, so behaviour cannot drift between them.

Supported inputs: **PDF**, **DOCX** (including tables), **TXT**, **Markdown**.
Supported providers: **Anthropic** (default), **OpenRouter**, and any **OpenAI-compatible**
endpoint including local runtimes like Ollama, vLLM and LM Studio.

---

## Design decisions worth knowing

These are the choices that shape everything else.

### The Pydantic model is the only source of truth

[`domain/resume.py`](src/resume_parser/domain/resume.py) defines the résumé once. From that
single definition the project derives the JSON Schema used to constrain the model's decoding,
the validator applied to the response, the FastAPI response model and OpenAPI contract, and
the typed object the pipeline operates on.

Add a field in one place and it propagates to all four. There is no second copy of the schema
to drift out of sync.

### Numbers are computed, never generated

Asking a model to "calculate total years of experience by combining all the work experience"
produces a number that is wrong in a specific way — it adds tenures, so a candidate who spent
two years contracting for three clients simultaneously comes out with six years. It also
changes between runs of the same document, which makes the field useless for filtering.

[`pipeline/enrichment.py`](src/resume_parser/pipeline/enrichment.py) merges the employment
date intervals and measures the union. Concurrent roles are counted once, the answer is exact,
and it is identical every time.

### Match scores are deterministic

[`pipeline/matching.py`](src/resume_parser/pipeline/matching.py) scores on the CPU, not
through a model. A hiring signal that changes between runs of the same inputs cannot be
audited, cannot be regression-tested, and — where hiring is regulated — cannot be defended.
Every sub-score is reproducible and ships with a rationale line explaining it.

An LLM is still the right tool for reading an unstructured job posting into a structured
`JobRequirements`; it just should not be the thing that produces the number.

### Failure is a first-class concern

Three independent layers, each for a different failure mode: retry with jittered backoff on
transient faults, fall through to the next model in the chain on a hard failure, and one JSON
repair pass before giving up. Deterministic errors short-circuit immediately rather than
burning the retry budget.

Every error is a typed exception carrying its own HTTP status, so a rate limit, a scanned PDF
and a bad API key are three distinguishable responses rather than one generic 500.

### Everything is observable

Each response carries the model that answered, token counts, an estimated cost, latency,
attempt count, whether a fallback was used, and whether it came from cache. Logs are
structured, correlated by request ID, and PII-redacted by default.

---

## Install

Requires **Python 3.12+**.

```bash
git clone https://github.com/NabiBukhsh-AI/Resume-AI-Parser.git
cd Resume-AI-Parser

# With uv (recommended)
uv venv && uv pip install -e ".[ui]"

# Or with pip
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[ui]"
```

Then set at least one provider credential:

```bash
cp .env.example .env
# edit .env, or just:
export ANTHROPIC_API_KEY=sk-ant-...
```

Check what the process actually sees:

```bash
resume-parser config
```

---

## Quick start

```bash
# Parse one résumé to stdout
resume-parser parse cv.pdf

# Human-readable digest instead of JSON
resume-parser parse cv.pdf --summary

# A whole directory, in parallel, one JSON file per document
resume-parser batch ./resumes --output ./parsed

# Score a candidate against a role
resume-parser match cv.pdf --job posting.txt

# Serve the API at http://localhost:8000/docs
resume-parser serve

# Serve the web UI at http://localhost:8501
resume-parser ui
```

---

## The HTTP API

```bash
resume-parser serve --port 8000
```

Interactive docs at `/docs`, the OpenAPI document at `/openapi.json`.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/parse` | Parse one résumé. |
| `POST` | `/v1/parse/batch` | Parse many concurrently; failures are reported per document. |
| `POST` | `/v1/match` | Score an already-parsed résumé against a job. |
| `POST` | `/v1/parse-and-match` | Upload a résumé and a posting; get both in one round trip. |
| `GET` | `/v1/schema` | The exact JSON Schema the model is constrained to. |
| `GET` | `/health` | Liveness. Never touches a dependency. |
| `GET` | `/health/ready` | Readiness. Returns 503 when no model has credentials. |

### Parsing

```bash
curl -X POST http://localhost:8000/v1/parse \
  -H "x-api-key: $RESUME_PARSER_API_KEY" \
  -F "file=@cv.pdf"
```

```jsonc
{
  "resume": {
    "contact": {
      "full_name": "Ada Lovelace",
      "first_name": "Ada",
      "last_name": "Lovelace",
      "email": "ada@example.com",
      "phone": "+14155550142",
      "location": "London, UK",
      "links": { "github": "https://github.com/ada", "linkedin": null, "other": [] }
    },
    "headline": "Senior Machine Learning Engineer",
    "summary": "Senior machine learning engineer with experience designing...",
    "experience": [
      {
        "job_title": "Senior Machine Learning Engineer",
        "company": "Analytical Engines Ltd",
        "start_date": "2021-01",
        "end_date": null,
        "is_current": true,
        "highlights": ["Shipped a ranking model serving 10M requests per day"],
        "technologies": ["Python", "PyTorch", "Kubernetes"]
      }
    ],
    "skills": [{ "name": "Python", "category": "programming_language", "proficiency": "expert" }],
    "analytics": {
      "total_years_of_experience": 7.8,   // computed in Python, not by the model
      "seniority_level": "senior",
      "current_company": "Analytical Engines Ltd",
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
    "content_sha256": "9f2c...",
    "page_count": 2,
    "truncated": false
  },
  "usage": {
    "provider": "anthropic",
    "model": "claude-opus-5",
    "tokens": { "input_tokens": 4210, "output_tokens": 1180, "cache_read_tokens": 1024 },
    "estimated_cost_usd": 0.0231,
    "latency_ms": 6820,
    "attempts": 1,
    "cached": false,
    "fallback_used": false
  },
  "warnings": []
}
```

### Matching

Pass structured `requirements` to score for free, or raw `job_description` to have the posting
structured first with one cheap model call.

```bash
curl -X POST http://localhost:8000/v1/match \
  -H "content-type: application/json" \
  -d '{
        "resume": { /* a previous /v1/parse response, verbatim */ },
        "requirements": {
          "required_skills": ["Python", "Kubernetes", "PyTorch"],
          "preferred_skills": ["Terraform"],
          "min_years_experience": 5,
          "seniority": "senior"
        }
      }'
```

```jsonc
{
  "match": {
    "score": 88.5,
    "breakdown": {
      "required_skills": 1.0,
      "preferred_skills": 0.0,
      "experience": 1.0,
      "seniority": 1.0,
      "education": 1.0
    },
    "matched_skills": ["Python", "Kubernetes", "PyTorch"],
    "gaps": [{ "skill": "Terraform", "required": false, "closest_match": null }],
    "meets_experience_bar": true,
    "rationale": [
      "Required skills: 3/3 met.",
      "7.8 years meets the 5-year minimum.",
      "Seniority matches the target level (senior)."
    ]
  }
}
```

### Errors

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
| --- | --- | --- |
| `invalid_document` | 415 | Not a supported document type. |
| `document_too_large` | 413 | Above `extraction.max_file_size`. |
| `empty_document` | 422 | Decoded, but too little text to parse. |
| `scanned_document` | 422 | A PDF with no text layer — needs OCR. |
| `unauthorized` | 401 | Missing or wrong `x-api-key`. |
| `rate_limited` | 429 | Local budget exhausted, or the provider rate-limited us. |
| `provider_not_configured` | 503 | No usable model credentials. |
| `all_providers_failed` | 502 | Every model in the chain failed. |
| `invalid_structured_output` | 502 | The model's payload did not satisfy the schema. |

---

## The CLI

| Command | What it does |
| --- | --- |
| `parse <file>` | Parse one document. `--summary` for a digest, `-o` to write JSON. |
| `batch <dir>` | Parse a directory concurrently. `-r` to recurse, `-o` for the output dir. |
| `match <file> --job <file>` | Parse and score against a posting. |
| `serve` | Run the API. `--reload` for development, `--workers N` for production. |
| `ui` | Run the Streamlit interface. |
| `schema` | Print the strict JSON Schema used to constrain the model. |
| `config` | Show the effective configuration and which providers are usable. |

```bash
# Parse a folder of applicants and keep the JSON
resume-parser batch ./applicants -o ./parsed -r

# Generate client types from the published contract
resume-parser schema -o resume.schema.json
```

---

## Configuration

Precedence, highest first: **environment variables** (and `.env`) → **YAML file** → **defaults**.

Nested keys use a double underscore: `llm.effort` is `RESUME_PARSER_LLM__EFFORT`.

```bash
export RESUME_PARSER_CONFIG_FILE=configs/default.yaml   # optional YAML layer
export RESUME_PARSER_LLM__EFFORT=high
export RESUME_PARSER_SERVER__PORT=9000
```

Credentials are deliberately **not** configurable through YAML — they are read from the
conventional provider variables so a secret never lands in a committable file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Anthropic credential. |
| `OPENROUTER_API_KEY` | — | OpenRouter credential. |
| `OPENAI_API_KEY` | — | OpenAI-compatible credential. |
| `RESUME_PARSER_API_KEY` | unset | Shared secret for `x-api-key`. Unset means the API is open. |
| `RESUME_PARSER_ENVIRONMENT` | `development` | `development` / `staging` / `production`. |
| `RESUME_PARSER_LLM__EFFORT` | `medium` | Reasoning depth: `low`…`max`. |
| `RESUME_PARSER_LLM__MAX_RETRIES` | `3` | Retries per model before falling through. |
| `RESUME_PARSER_EXTRACTION__MAX_FILE_SIZE` | `16777216` | Upload ceiling in bytes. |
| `RESUME_PARSER_SERVER__RATE_LIMIT_PER_MINUTE` | `60` | Per-client budget; `0` disables. |
| `RESUME_PARSER_CACHE__DIRECTORY` | unset | Enables the disk cache tier. |
| `RESUME_PARSER_OBSERVABILITY__JSON_LOGS` | `false` | JSON lines for log aggregators. |
| `RESUME_PARSER_OBSERVABILITY__REDACT_PII` | `true` | Scrub PII from logs. |

See [`configs/default.yaml`](configs/default.yaml) for the annotated full set.

### The model chain

Models are tried in order; entries whose provider has no credentials are skipped
automatically, so you can list several and set only the key you have.

```yaml
llm:
  models:
    - provider: anthropic
      model: claude-opus-5
      input_cost_per_mtok: 5.0
      output_cost_per_mtok: 25.0
    - provider: openrouter          # cheaper fallback
      model: google/gemini-2.5-flash
```

Prices drive the cost estimate in every response. Omit them and the estimate is reported as
`null` rather than as a wrong number.

---

## Architecture

```
                    ┌──────────┐  ┌──────────┐  ┌───────────┐
                    │ FastAPI  │  │   CLI    │  │ Streamlit │   three surfaces,
                    └────┬─────┘  └────┬─────┘  └─────┬─────┘   zero business logic
                         └─────────────┼──────────────┘
                                       ▼
                        ┌──────────────────────────────┐
                        │    ResumeParsingService      │  the single orchestrator
                        └──────────────┬───────────────┘
                                       ▼
   bytes ──► ExtractionService ──► ParseCache ──► LLMClient ──► Pydantic ──► normalize ──► enrich
             ├ detect format       content-      ├ retry        validate     ISO dates    merge date
             ├ pypdf / python-docx addressed     ├ fallback     or reject    canonical    intervals,
             └ reject scans/junk   (bytes+model+ └ JSON repair               skills       seniority,
                                    prompt+schema)                                        completeness
```

```
src/resume_parser/
├── domain/          # Pydantic models — the single source of truth
├── extraction/      # bytes → text; format detection, PDF/DOCX/text readers
├── llm/             # provider abstraction, strict schema generation, resilience
│   ├── base.py              # the LLMProvider protocol — one method to add a backend
│   ├── schema.py            # Pydantic → strict JSON Schema
│   ├── anthropic_provider.py
│   ├── openai_compatible.py # covers OpenRouter, OpenAI, Ollama, vLLM, LM Studio
│   └── client.py            # retries, model fallback, JSON repair, cost accounting
├── pipeline/        # normalization, deterministic enrichment, caching, matching
├── api/             # FastAPI app factory, routers, middleware, error mapping
├── ui/              # Streamlit view layer
└── cli.py           # Typer CLI
```

### Adding a provider

Implement one method:

```python
class MyProvider:
    name = "myprovider"

    async def generate(
        self, request: StructuredRequest, spec: ModelSpec
    ) -> StructuredResponse: ...  # call your backend, return validated JSON + token usage

    async def aclose(self) -> None: ...
```

Register it in `build_providers()` and add it to the chain. Nothing else changes — the
pipeline, the retry policy and all three surfaces are provider-agnostic.

### Adding a document format

Implement `TextExtractor` and register it. An OCR backend for scanned PDFs slots in here
without touching `ExtractionService`:

```python
service.register(MyOcrExtractor())
```

---

## The data model

```
Resume
├── contact          full/first/last name, email, phone, location, links
├── headline         current or most recent title
├── summary          stated, or written from the document if absent
├── experience[]     title, company, type, location, dates, is_current,
│                    description, highlights[], technologies[]
├── education[]      degree, field, institution, dates, grade, description
├── skills[]         name, category, proficiency, years
├── certifications[] name, issuer, dates, credential id
├── projects[]       name, description, role, url, stack, dates
├── languages[]      name, fluency
├── awards[] · publications[]
└── analytics        ← computed in Python, never asked of the model
    ├── total_years_of_experience   overlapping roles merged, counted once
    ├── seniority_level             from titles, with tenure as tiebreaker
    ├── average_tenure_years        completed roles only
    ├── career_gaps_months          months no role covers
    ├── top_skills                  ranked by weighted evidence
    ├── completeness_score          0–1, weighted section coverage
    └── missing_sections
```

Fetch the machine-readable contract at `GET /v1/schema` or with `resume-parser schema`.

---

## Job matching

Five weighted dimensions, each normalized to 0–1:

| Dimension | Weight | How it scores |
| --- | --- | --- |
| Required skills | 45% | Fraction met. Aliases resolve, so `k8s` satisfies `Kubernetes`. |
| Preferred skills | 15% | Same, on the nice-to-haves. |
| Experience | 20% | Full marks at or above the bar; partial credit below it. |
| Seniority | 10% | Distance on an ordinal ladder, −25% per level. |
| Education | 10% | Highest degree held vs. degree requested. |

Weights are overridable per call. A dimension the posting does not state scores 1.0 — it
cannot discriminate, so it should not penalise.

Skills are gathered from the skills section, per-role technology lists, project stacks and
certifications: a tool used in a job but missing from the keyword list still counts. A near
miss is reported as `closest_match` rather than silently counted as a hit, so a human sees it.

---

## Deployment

```bash
docker compose up --build          # API on :8000
docker compose --profile ui up     # plus the UI on :8501
```

Or directly:

```bash
docker build -t resume-ai-parser .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... resume-ai-parser
```

The image is multi-stage, runs as a non-root user (uid 10001), carries no build tools or
source tree, and its healthcheck hits `/health/ready` — so an instance without working
credentials is kept out of the load balancer instead of serving guaranteed failures.

### Production checklist

- [ ] Set `RESUME_PARSER_API_KEY`, or terminate auth at your gateway.
- [ ] `RESUME_PARSER_ENVIRONMENT=production` and leave `debug` off.
- [ ] `RESUME_PARSER_OBSERVABILITY__JSON_LOGS=true`; keep `REDACT_PII=true`.
- [ ] Set `RESUME_PARSER_SERVER__CORS_ORIGINS` to exact origins if a browser calls the API.
- [ ] Probe `/health` for liveness and `/health/ready` for readiness — they are different questions.
- [ ] Running more than one instance? The built-in limiter is **per-process**. Set
      `RATE_LIMIT_PER_MINUTE=0` and rate-limit at the ingress.
- [ ] Mount a volume for `RESUME_PARSER_CACHE__DIRECTORY` so restarts do not re-pay for
      documents already parsed.

---

## Development

```bash
uv pip install -e ".[dev,ui]"

pytest                     # 207 tests, no network, no API key needed
pytest --cov               # with coverage
ruff check . && ruff format --check .
mypy                       # strict, across the whole package
```

The suite runs entirely on stub providers. That is deliberate: tests that hit a real model are
slow, flaky and billable, so the parts worth testing continuously — extraction, normalization,
date arithmetic, matching, resilience, and the HTTP contract — are isolated from the one part
that cannot be tested cheaply. For that part, see below.

---

## Evaluation

Unit tests prove the deterministic half is correct. They say nothing about whether the *model*
is extracting well — and that is what regresses when you change a prompt, switch models, or
adjust reasoning effort.

```bash
python evals/run_eval.py --output evals/results/baseline.json
# ... change a prompt ...
python evals/run_eval.py --baseline evals/results/baseline.json
```

Reports field accuracy, company/title/skill recall and precision, numeric error, latency and
cost — and exits non-zero when quality moves backwards. See [`evals/README.md`](evals/README.md).

---

## Privacy

Résumés are personal data. The defaults reflect that:

- **Nothing is written to disk.** Uploads are processed in memory. (The old version wrote
  every upload to an `uploads/` folder under the client-supplied filename.)
- **Logs are redacted** — emails, phone numbers, credentials and document bodies are scrubbed
  by a structlog processor before anything reaches a sink.
- **Error bodies do not echo your payload.**
- **`.gitignore` excludes `*.pdf` and `*.docx`** so candidate documents are not committed by
  accident.

The result cache is content-addressed and holds parsed output. If that matters for your
retention policy, set `cache.ttl_seconds`, or turn it off.

---

## Migrating from 1.x

Version 2.0 is a full rewrite. The 1.x tree did not run — `api.py` and `ui_components.py`
both imported a `file_utils` module that was absent from the repository.

| 1.x | 2.0 |
| --- | --- |
| `POST /parse` | `POST /v1/parse` |
| `{talent: {...}}` | `{resume: {...}, document: {...}, usage: {...}}` |
| `first_name`, `last_name` at top level | under `contact` |
| `total_years_of_experience` (model-generated) | `analytics.total_years_of_experience` (computed) |
| `current_title` | `headline` |
| `streamlit run main.py` | `resume-parser ui` |
| `uvicorn api:app` | `resume-parser serve` |
| `config.yml` (required, CWD-relative) | `configs/default.yaml` (optional) + env vars |

Other notable changes: the `response_format` payload was malformed, so structured output was
silently ignored and every response was salvaged with `content.find("{")`; there was no request
timeout; API keys were compared with `!=`, which is timing-attackable; and `python-magic-bin`
pinned the project to Windows. All fixed.

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Nabi Bukhsh Baloch](https://github.com/NabiBukhsh-AI). Issues and pull requests
welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
