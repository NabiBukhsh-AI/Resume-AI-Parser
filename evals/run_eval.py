#!/usr/bin/env python
"""Offline evaluation harness for the extraction pipeline.

Prompt and model changes are not free: a wording tweak that looks better on one resume can
quietly regress ten others, and nothing in a unit-test suite will catch it. This harness
scores the pipeline against a labelled dataset so a change can be judged on numbers rather
than on a spot check.

Metrics, and why these:

* **field accuracy** - exact match on scalar identity fields (name, email, phone). These
  are the fields an ATS import will get wrong in a visible, embarrassing way.
* **section recall/precision** - set overlap on companies, titles and skills. Recall
  matters more than precision for sourcing; both are reported so a change that inflates
  recall by hallucinating is visible.
* **numeric error** - absolute error on total years of experience. Computed in Python, so
  this should be exactly 0 unless date *extraction* regressed - which is what makes it a
  clean signal about the model rather than about our arithmetic.

Usage:

    python evals/run_eval.py --dataset evals/datasets/sample.jsonl
    python evals/run_eval.py --dataset ... --baseline evals/results/main.json

Every run costs real money, because it calls a real provider. Keep datasets small and
representative rather than large.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Allow running this file directly from a checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from resume_parser.domain.results import ParseResult
from resume_parser.pipeline.parser import ResumeParsingService
from resume_parser.settings import get_settings


@dataclass(slots=True)
class Example:
    """One labelled document."""

    document: Path
    expected: dict[str, Any]

    @property
    def name(self) -> str:
        return self.document.name


@dataclass(slots=True)
class CaseScore:
    """Per-document scores."""

    name: str
    field_accuracy: float
    company_recall: float
    title_recall: float
    skill_recall: float
    skill_precision: float
    years_error: float
    completeness: float
    latency_ms: int
    cost_usd: float | None
    failures: list[str] = field(default_factory=list)
    error: str | None = None


def _norm(value: Any) -> str:
    """Case- and whitespace-insensitive form used for every comparison."""
    return " ".join(str(value or "").split()).casefold()


def _recall(expected: Iterable[str], actual: Iterable[str]) -> float:
    """Fraction of expected items present in ``actual``, by substring containment.

    Containment rather than equality: 'Analytical Engines Ltd' should count as a hit for
    an expected 'Analytical Engines', and penalising the suffix would measure formatting
    rather than extraction.
    """
    wanted = [_norm(item) for item in expected if _norm(item)]
    if not wanted:
        return 1.0
    found = [_norm(item) for item in actual]
    hits = sum(any(want in got or got in want for got in found) for want in wanted)
    return hits / len(wanted)


def _score(example: Example, result: ParseResult) -> CaseScore:
    """Compare one parse against its labels."""
    resume = result.resume
    expected = example.expected
    failures: list[str] = []

    scalar_checks = {
        "full_name": resume.contact.full_name,
        "email": resume.contact.email,
        "phone": resume.contact.phone,
        "location": resume.contact.location,
    }
    compared = 0
    correct = 0
    for key, actual in scalar_checks.items():
        if key not in expected:
            continue
        compared += 1
        if _norm(expected[key]) == _norm(actual):
            correct += 1
        else:
            failures.append(f"{key}: expected {expected[key]!r}, got {actual!r}")

    expected_skills = expected.get("skills", [])
    actual_skills = [skill.name for skill in resume.skills]
    skill_recall = _recall(expected_skills, actual_skills)
    skill_precision = _recall(actual_skills, expected_skills) if expected_skills else 1.0

    years_error = 0.0
    if (want_years := expected.get("total_years_of_experience")) is not None:
        years_error = abs(float(want_years) - resume.analytics.total_years_of_experience)
        if years_error > 0.5:
            failures.append(
                f"years: expected {want_years}, got {resume.analytics.total_years_of_experience}"
            )

    return CaseScore(
        name=example.name,
        field_accuracy=correct / compared if compared else 1.0,
        company_recall=_recall(expected.get("companies", []), resume.analytics.companies),
        title_recall=_recall(
            expected.get("titles", []), [role.job_title or "" for role in resume.experience]
        ),
        skill_recall=skill_recall,
        skill_precision=skill_precision,
        years_error=years_error,
        completeness=resume.analytics.completeness_score,
        latency_ms=result.usage.latency_ms,
        cost_usd=result.usage.estimated_cost_usd,
        failures=failures,
    )


def load_dataset(path: Path) -> list[Example]:
    """Read a JSONL dataset.

    Each line is ``{"document": "<path>", "expected": {...}}``; document paths are resolved
    relative to the dataset file so a dataset directory stays portable.
    """
    examples: list[Example] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            msg = f"{path}:{number} is not valid JSON: {exc}"
            raise SystemExit(msg) from exc
        document = (path.parent / record["document"]).resolve()
        if not document.is_file():
            msg = f"{path}:{number} references a missing document: {document}"
            raise SystemExit(msg)
        examples.append(Example(document=document, expected=record.get("expected", {})))
    if not examples:
        raise SystemExit(f"{path} contains no examples")
    return examples


async def run(examples: Sequence[Example]) -> list[CaseScore]:
    """Parse every example and score it."""
    settings = get_settings()
    if not settings.configured_models():
        raise SystemExit(
            "No provider credentials found. Set ANTHROPIC_API_KEY (or OPENROUTER_API_KEY / "
            "OPENAI_API_KEY) before running an evaluation."
        )

    service = ResumeParsingService(settings)
    scores: list[CaseScore] = []
    try:
        for example in examples:
            started = time.perf_counter()
            try:
                result = await service.parse(example.document.read_bytes(), filename=example.name)
            except Exception as exc:
                scores.append(
                    CaseScore(
                        name=example.name,
                        field_accuracy=0.0,
                        company_recall=0.0,
                        title_recall=0.0,
                        skill_recall=0.0,
                        skill_precision=0.0,
                        years_error=float("nan"),
                        completeness=0.0,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        cost_usd=None,
                        error=str(exc),
                    )
                )
                continue
            scores.append(_score(example, result))
    finally:
        await service.aclose()
    return scores


def summarize(scores: Sequence[CaseScore]) -> dict[str, Any]:
    """Aggregate per-case scores into headline numbers."""
    successful = [score for score in scores if score.error is None]
    if not successful:
        return {"cases": len(scores), "failed": len(scores)}

    def mean(attribute: str) -> float:
        return round(statistics.fmean(getattr(s, attribute) for s in successful), 4)

    costs = [s.cost_usd for s in successful if s.cost_usd is not None]
    return {
        "cases": len(scores),
        "failed": len(scores) - len(successful),
        "field_accuracy": mean("field_accuracy"),
        "company_recall": mean("company_recall"),
        "title_recall": mean("title_recall"),
        "skill_recall": mean("skill_recall"),
        "skill_precision": mean("skill_precision"),
        "mean_years_error": mean("years_error"),
        "completeness": mean("completeness"),
        "p50_latency_ms": int(statistics.median(s.latency_ms for s in successful)),
        "total_cost_usd": round(sum(costs), 4) if costs else None,
    }


def _print_report(scores: Sequence[CaseScore], summary: dict[str, Any]) -> None:
    """Write a plain-text report to stdout."""
    print("\n=== Per-document ===")
    for score in scores:
        if score.error:
            print(f"  {score.name:<32} FAILED  {score.error}")
            continue
        print(
            f"  {score.name:<32} fields={score.field_accuracy:.0%}  "
            f"skills={score.skill_recall:.0%}  companies={score.company_recall:.0%}  "
            f"years_err={score.years_error:.1f}"
        )
        for failure in score.failures:
            print(f"      - {failure}")

    print("\n=== Summary ===")
    for key, value in summary.items():
        print(f"  {key:<20} {value}")


def _compare(summary: dict[str, Any], baseline_path: Path) -> int:
    """Report movement against a saved baseline. Returns a process exit code."""
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")).get("summary", {})
    print(f"\n=== Against {baseline_path} ===")
    regressed = False
    # Higher is better for every metric except these two.
    lower_is_better = {"mean_years_error", "failed", "p50_latency_ms", "total_cost_usd"}

    for key, value in summary.items():
        before = baseline.get(key)
        if not isinstance(value, int | float) or not isinstance(before, int | float):
            continue
        delta = value - before
        if abs(delta) < 1e-9:
            marker = "="
        elif (delta < 0) is (key not in lower_is_better):
            marker = "REGRESSED"
            # Latency and cost move with provider load, so they are reported but never
            # treated as a regression on their own.
            regressed = regressed or key not in {"p50_latency_ms", "total_cost_usd"}
        else:
            marker = "improved"
        print(f"  {key:<20} {before} -> {value}  ({delta:+.4f}) {marker}")

    return 1 if regressed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=Path(__file__).parent / "datasets" / "sample.jsonl"
    )
    parser.add_argument("--output", type=Path, help="Write the full report as JSON.")
    parser.add_argument("--baseline", type=Path, help="Compare against a previous report.")
    args = parser.parse_args()

    examples = load_dataset(args.dataset)
    print(f"Evaluating {len(examples)} documents from {args.dataset}...")

    scores = asyncio.run(run(examples))
    summary = summarize(scores)
    _print_report(scores, summary)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"summary": summary, "cases": [asdict(s) for s in scores]}, indent=2),
            encoding="utf-8",
        )
        print(f"\nWrote {args.output}")

    if args.baseline:
        return _compare(summary, args.baseline)
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
