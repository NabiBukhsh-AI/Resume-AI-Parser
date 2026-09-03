## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## Why

<!-- The reasoning or the trade-off. This is the part reviewers need most. -->

## Checklist

- [ ] `make check` passes (ruff, ruff format, mypy strict, pytest)
- [ ] Tests cover the change
- [ ] Docs updated if behaviour changed (README, CHANGELOG, docstrings)
- [ ] No real résumés, credentials, or other personal data committed

## If this touches a prompt or model

Prompt changes are behaviour changes that unit tests do not cover.

- [ ] `PROMPT_VERSION` bumped in `llm/prompts.py`
- [ ] Evaluation run before and after — paste the numbers:

```
python evals/run_eval.py --baseline before.json
```
