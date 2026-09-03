"""Signals computed from a resume, in Python, with no model involved.

The single biggest accuracy improvement over the original design lives here. That version
asked the LLM to "calculate the total years of experience by combining all the work
experience". Language models are bad at this in a specific and expensive way: they add
tenures, so a candidate who spent two years contracting for three clients simultaneously
comes out with six years of experience. The number also drifts between runs of the same
document, which makes the field useless for filtering or ranking.

Merging date intervals is a five-line algorithm with an exact answer. Use the model for
reading comprehension; use the CPU for arithmetic.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from itertools import pairwise

from resume_parser.domain.enums import SeniorityLevel
from resume_parser.domain.resume import Resume, ResumeAnalytics, ResumeExtraction
from resume_parser.pipeline.normalization import parse_partial_date

__all__ = [
    "build_analytics",
    "enrich",
    "infer_seniority",
    "merge_intervals",
    "total_experience_years",
]

_DAYS_PER_YEAR = 365.25
_DAYS_PER_MONTH = 30.44

#: Title keywords mapped to a seniority level, checked longest-first so "senior staff"
#: does not match on "senior".
_TITLE_SIGNALS: tuple[tuple[SeniorityLevel, tuple[str, ...]], ...] = (
    (SeniorityLevel.EXECUTIVE, ("chief", "cto", "ceo", "cfo", "coo", "vp ", "vice president",
                                "head of", "director", "founder", "partner")),
    (SeniorityLevel.PRINCIPAL, ("principal", "distinguished", "fellow", "architect")),
    (SeniorityLevel.LEAD, ("lead", "manager", "supervisor", "team lead", "tech lead")),
    (SeniorityLevel.SENIOR, ("senior", "sr.", "sr ", "staff")),
    (SeniorityLevel.INTERN, ("intern", "trainee", "apprentice", "co-op")),
    (SeniorityLevel.JUNIOR, ("junior", "jr.", "jr ", "associate", "entry")),
)  # fmt: skip

#: Sections that carry real signal, and their weight in the completeness score.
_COMPLETENESS_WEIGHTS: dict[str, float] = {
    "contact.email": 0.15,
    "contact.full_name": 0.15,
    "experience": 0.25,
    "skills": 0.15,
    "education": 0.10,
    "summary": 0.10,
    "contact.phone": 0.05,
    "contact.location": 0.05,
}


def merge_intervals(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Merge overlapping or adjacent ``(start, end)`` pairs into disjoint spans.

    This is what stops concurrent roles from being counted twice.
    """
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda pair: pair[0])
    merged: list[tuple[date, date]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _role_intervals(resume: ResumeExtraction, today: date) -> list[tuple[date, date]]:
    """Resolve each role to a concrete date range, skipping ones we cannot date."""
    intervals: list[tuple[date, date]] = []
    for role in resume.experience:
        start = parse_partial_date(role.start_date)
        if start is None:
            continue
        end = today if role.is_current else parse_partial_date(role.end_date)
        if end is None:
            # Undated end on a non-current role: assume it ran to the start, which
            # contributes nothing rather than inventing tenure.
            end = start
        if end < start:
            start, end = end, start
        intervals.append((start, min(end, today)))
    return intervals


def total_experience_years(resume: ResumeExtraction, *, today: date | None = None) -> float:
    """Total professional experience in years, with overlapping roles counted once."""
    reference = today or datetime.now(UTC).date()
    spans = merge_intervals(_role_intervals(resume, reference))
    days = sum((end - start).days for start, end in spans)
    return round(days / _DAYS_PER_YEAR, 1)


def _career_gap_months(intervals: list[tuple[date, date]]) -> int:
    """Months between the first and last role that no role covers."""
    merged = merge_intervals(intervals)
    if len(merged) < 2:
        return 0
    gap_days = sum((nxt[0] - cur[1]).days for cur, nxt in pairwise(merged))
    return max(0, round(gap_days / _DAYS_PER_MONTH))


def infer_seniority(resume: ResumeExtraction, years: float) -> SeniorityLevel:
    """Infer career stage from the most recent title, with tenure as the tiebreaker.

    Titles win when they are explicit, because "Senior Engineer with 3 years" is a real
    thing and the title is the claim the candidate is making. Tenure only decides cases
    where no title carries a signal.
    """
    for role in resume.experience[:3]:
        title = (role.job_title or "").lower()
        if not title:
            continue
        for level, keywords in _TITLE_SIGNALS:
            if any(keyword in title for keyword in keywords):
                return level

    if not resume.experience and years == 0:
        return SeniorityLevel.UNKNOWN
    if years < 1:
        return SeniorityLevel.ENTRY
    if years < 3:
        return SeniorityLevel.JUNIOR
    if years < 6:
        return SeniorityLevel.MID
    if years < 10:
        return SeniorityLevel.SENIOR
    return SeniorityLevel.LEAD


def _rank_top_skills(resume: ResumeExtraction, limit: int = 12) -> list[str]:
    """Rank skills by how much of the document actually evidences them.

    A skill named in three roles is stronger signal than one that appears only in a
    keyword list, so evidence counts are weighted by where they came from.
    """
    scores: Counter[str] = Counter()
    for skill in resume.skills:
        scores[skill.name] += 2
        if skill.proficiency is not None:
            scores[skill.name] += 1
    for role in resume.experience:
        for tech in role.technologies:
            scores[tech] += 3
    for project in resume.projects:
        for tech in project.technologies:
            scores[tech] += 1
    return [name for name, _ in scores.most_common(limit)]


def _completeness(resume: ResumeExtraction) -> tuple[float, list[str]]:
    """Weighted fraction of high-value sections that came back populated."""
    present: dict[str, bool] = {
        "contact.email": bool(resume.contact.email),
        "contact.full_name": bool(resume.contact.full_name),
        "contact.phone": bool(resume.contact.phone),
        "contact.location": bool(resume.contact.location),
        "experience": bool(resume.experience),
        "education": bool(resume.education),
        "skills": bool(resume.skills),
        "summary": bool(resume.summary),
    }
    score = sum(weight for key, weight in _COMPLETENESS_WEIGHTS.items() if present[key])
    missing = sorted(key for key, ok in present.items() if not ok)
    return round(score, 3), missing


def build_analytics(resume: ResumeExtraction, *, today: date | None = None) -> ResumeAnalytics:
    """Compute every derived signal for ``resume``."""
    reference = today or datetime.now(UTC).date()
    intervals = _role_intervals(resume, reference)
    merged = merge_intervals(intervals)
    years = round(sum((end - start).days for start, end in merged) / _DAYS_PER_YEAR, 1)

    completed = [
        (end - start).days / _DAYS_PER_YEAR
        for role, (start, end) in zip(resume.experience, intervals, strict=False)
        if not role.is_current and end > start
    ]
    current = next((role for role in resume.experience if role.is_current), None)
    if current is None and resume.experience:
        current = resume.experience[0]

    companies: list[str] = []
    for role in resume.experience:
        if role.company and role.company not in companies:
            companies.append(role.company)

    completeness, missing = _completeness(resume)
    return ResumeAnalytics(
        total_years_of_experience=years,
        seniority_level=infer_seniority(resume, years),
        current_position=current.job_title if current else None,
        current_company=current.company if current else None,
        companies=companies,
        top_skills=_rank_top_skills(resume),
        average_tenure_years=(round(sum(completed) / len(completed), 1) if completed else None),
        career_gaps_months=_career_gap_months(intervals),
        completeness_score=completeness,
        missing_sections=missing,
    )


def enrich(extraction: ResumeExtraction, *, today: date | None = None) -> Resume:
    """Promote a validated extraction to a full :class:`Resume` with analytics attached."""
    return Resume(
        **extraction.model_dump(),
        analytics=build_analytics(extraction, today=today),
    )
