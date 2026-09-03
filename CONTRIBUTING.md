# Contributing

Thanks for taking an interest. Issues and pull requests are welcome.

## Getting set up

```bash
git clone https://github.com/NabiBukhsh-AI/Resume-AI-Parser.git
cd Resume-AI-Parser
uv venv && uv pip install -e ".[dev,ui]"
pre-commit install          # optional but recommended
```

The test suite needs no API key and makes no network calls:

```bash
make check     # lint + format + strict types + tests
```

## What CI enforces

- `ruff check .` and `ruff format --check .`
- `mypy` in strict mode across the whole package
- `pytest` on Python 3.12 and 3.13, plus Windows and macOS on 3.13
- The wheel installs and the CLI runs
- The Docker image builds and starts

## House rules

**The domain model is the source of truth.** Add a field to `domain/resume.py` and it
propagates to the LLM schema, validation, the API contract and the pipeline types. Never
hand-write a second copy of the schema.

**Do not ask a model to compute.** Anything derivable from extracted data — dates, counts,
scores, rankings — belongs in `pipeline/enrichment.py` or `pipeline/matching.py`. Models read;
Python calculates. This is the project's main accuracy guarantee.

**Keep surfaces thin.** The API routers, the CLI and the Streamlit app must contain no business
logic. If behaviour needs changing, it changes in `ResumeParsingService` so all three inherit it.

**Errors are typed.** Raise something from `resume_parser.exceptions`; the API layer maps it to
a status code. Do not raise `HTTPException` outside the API layer, and do not add try/except to
route handlers.

**Comment the why, not the what.** The codebase explains reasoning and trade-offs, not syntax.

**Never commit a real résumé.** They are personal data. Use synthetic documents; `.gitignore`
and a pre-commit hook block `.pdf` and `.docx` by default.

## Adding a provider

Implement the `LLMProvider` protocol in `llm/base.py`, translate the backend's errors into the
`resume_parser.exceptions` hierarchy so the retry policy can act on them, and register it in
`build_providers()`. Add tests using `respx` (see `tests/test_llm.py`) — no live calls.

## Adding a document format

Implement `TextExtractor` in `extraction/base.py`, add the format to `DocumentFormat`, teach
`detection.py` to recognise it from content, and register the extractor. OCR for scanned PDFs
is an open and welcome contribution.

## Changing a prompt

Prompt changes are behaviour changes and are not covered by unit tests. Bump `PROMPT_VERSION`
in `llm/prompts.py` — it is part of the cache key, so stale results are invalidated — and run
the evaluation harness before and after:

```bash
python evals/run_eval.py --output before.json
# make your change
python evals/run_eval.py --baseline before.json
```

Include the before/after numbers in the pull request.

## Pull requests

Branch from `main`, keep the change focused, make sure `make check` passes, and describe what
changed and why. Conventional-commit style (`feat:`, `fix:`, `docs:`, `refactor:`) is preferred
but not enforced.
