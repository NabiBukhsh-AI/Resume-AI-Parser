"""Explainable resume-to-job matching.

Scoring is deterministic and runs entirely on the CPU. That is a design decision, not a
shortcut: a hiring signal that changes between runs of the same inputs cannot be audited,
cannot be regression-tested, and - where hiring decisions are regulated - cannot be
defended. Every sub-score here is reproducible and comes with a written rationale.

An LLM is still the right tool for reading an unstructured job posting into
:class:`JobRequirements`; it just should not be the thing that produces the number.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from resume_parser.domain.enums import SeniorityLevel
from resume_parser.domain.matching import (
    JobRequirements,
    MatchBreakdown,
    MatchResult,
    SkillGap,
)
from resume_parser.domain.resume import Resume
from resume_parser.pipeline.normalization import canonical_skill_name

__all__ = ["DEFAULT_WEIGHTS", "match_resume_to_job"]

#: Relative importance of each dimension. Must sum to 1.0.
DEFAULT_WEIGHTS: dict[str, float] = {
    "required_skills": 0.45,
    "preferred_skills": 0.15,
    "experience": 0.20,
    "seniority": 0.10,
    "education": 0.10,
}

#: Ordinal ranking used to measure distance between seniority levels.
_SENIORITY_RANK: dict[SeniorityLevel, int] = {
    SeniorityLevel.INTERN: 0,
    SeniorityLevel.ENTRY: 1,
    SeniorityLevel.JUNIOR: 2,
    SeniorityLevel.MID: 3,
    SeniorityLevel.SENIOR: 4,
    SeniorityLevel.LEAD: 5,
    SeniorityLevel.PRINCIPAL: 6,
    SeniorityLevel.EXECUTIVE: 7,
}

#: Below this ratio two skill names are considered different skills.
_FUZZY_THRESHOLD = 0.86

_DEGREE_RE = re.compile(
    r"\b(phd|doctorate|master|msc|m\.s|mba|bachelor|bsc|b\.s|b\.?tech|associate|diploma)\b",
    re.IGNORECASE,
)
_DEGREE_RANK = {
    "diploma": 1, "associate": 1,
    "bachelor": 2, "bsc": 2, "b.s": 2, "btech": 2, "b.tech": 2,
    "master": 3, "msc": 3, "m.s": 3, "mba": 3,
    "phd": 4, "doctorate": 4,
}  # fmt: skip


def _candidate_skill_index(resume: Resume) -> dict[str, str]:
    """Every skill the candidate evidences, keyed by lower-cased canonical name.

    Pulls from the skills section, per-role technologies and project stacks - a skill used
    in a job but missing from the keyword list is still a skill the candidate has.
    """
    index: dict[str, str] = {}

    def add(raw: str) -> None:
        canonical = canonical_skill_name(raw)
        if canonical:
            index.setdefault(canonical.lower(), canonical)

    for skill in resume.skills:
        add(skill.name)
    for role in resume.experience:
        for tech in role.technologies:
            add(tech)
    for project in resume.projects:
        for tech in project.technologies:
            add(tech)
    for cert in resume.certifications:
        add(cert.name)
    return index


def _find_skill(requirement: str, index: dict[str, str]) -> tuple[str | None, str | None]:
    """Locate ``requirement`` in the candidate's skills.

    Returns ``(match, closest)``. An exact or substring hit is a match; anything above the
    fuzzy threshold is reported as a *closest* alternative rather than a match, so a near
    miss is surfaced to a human instead of being quietly counted as a hit.
    """
    wanted = canonical_skill_name(requirement).lower()
    if not wanted:
        return None, None
    if wanted in index:
        return index[wanted], None

    # "AWS Lambda" satisfies a requirement for "AWS"; guard against one-token noise.
    for key, original in index.items():
        if len(wanted) >= 3 and (wanted in key or key in wanted):
            return original, None

    best_name: str | None = None
    best_ratio = 0.0
    for key, original in index.items():
        ratio = SequenceMatcher(None, wanted, key).ratio()
        if ratio > best_ratio:
            best_ratio, best_name = ratio, original
    if best_ratio >= _FUZZY_THRESHOLD:
        return best_name, None
    return None, best_name if best_ratio >= 0.6 else None


def _score_skills(
    requirements: list[str], index: dict[str, str], *, required: bool
) -> tuple[float, list[str], list[SkillGap]]:
    """Fraction of ``requirements`` the candidate meets, plus matches and gaps."""
    if not requirements:
        # No requirements stated is not a failure; it is a dimension that cannot discriminate.
        return 1.0, [], []
    matched: list[str] = []
    gaps: list[SkillGap] = []
    for requirement in requirements:
        hit, closest = _find_skill(requirement, index)
        if hit:
            matched.append(requirement)
        else:
            gaps.append(SkillGap(skill=requirement, required=required, closest_match=closest))
    return len(matched) / len(requirements), matched, gaps


def _score_experience(years: float, minimum: float | None) -> tuple[float, bool, str]:
    """Score years of experience against the posting's floor."""
    if minimum is None:
        return 1.0, True, "No minimum experience stated; dimension not scored."
    if years >= minimum:
        return 1.0, True, f"{years} years meets the {minimum}-year minimum."
    if minimum <= 0:
        return 1.0, True, "Minimum experience is zero."
    ratio = max(0.0, years / minimum)
    # Partial credit: 4 of 5 years is a near-miss, not a disqualification.
    return round(ratio, 3), False, f"{years} years against a {minimum}-year minimum."


def _score_seniority(candidate: SeniorityLevel, target: SeniorityLevel | None) -> tuple[float, str]:
    """Score how close the candidate's level is to the target level."""
    if target is None or candidate is SeniorityLevel.UNKNOWN:
        return 1.0, "Seniority not stated on one side; dimension not scored."
    candidate_rank = _SENIORITY_RANK.get(candidate)
    target_rank = _SENIORITY_RANK.get(target)
    if candidate_rank is None or target_rank is None:
        return 1.0, "Seniority could not be ranked."
    distance = abs(candidate_rank - target_rank)
    score = max(0.0, 1.0 - distance * 0.25)
    direction = "above" if candidate_rank > target_rank else "below"
    if distance == 0:
        return 1.0, f"Seniority matches the target level ({target.value})."
    return round(score, 3), (
        f"Candidate is {distance} level(s) {direction} the target ({target.value})."
    )


def _score_education(resume: Resume, requirement: str | None) -> tuple[float, str]:
    """Score the highest degree held against the degree the posting asks for."""
    if not requirement:
        return 1.0, "No education requirement stated; dimension not scored."
    required_match = _DEGREE_RE.search(requirement)
    if not required_match:
        return 1.0, "Education requirement did not name a recognisable degree level."
    required_rank = _DEGREE_RANK.get(required_match.group(1).lower(), 2)

    held = 0
    for entry in resume.education:
        text = f"{entry.degree or ''} {entry.field_of_study or ''}"
        if degree_match := _DEGREE_RE.search(text):
            held = max(held, _DEGREE_RANK.get(degree_match.group(1).lower(), 0))

    if held == 0:
        return 0.0, "No recognisable degree found on the resume."
    if held >= required_rank:
        return 1.0, "Education requirement met."
    return 0.5, "Candidate holds a lower degree level than requested."


def match_resume_to_job(
    resume: Resume,
    job: JobRequirements,
    *,
    weights: dict[str, float] | None = None,
) -> MatchResult:
    """Score ``resume`` against ``job`` and explain every component.

    Args:
        resume: A parsed and enriched resume.
        job: Structured requirements for the role.
        weights: Override the per-dimension weights. Values are re-normalized, so they do
            not have to sum to 1.

    Returns:
        A :class:`MatchResult` whose ``score`` is 0-100 and whose ``rationale`` explains
        how each dimension contributed.
    """
    active = dict(weights or DEFAULT_WEIGHTS)
    total_weight = sum(active.values()) or 1.0
    index = _candidate_skill_index(resume)
    years = resume.analytics.total_years_of_experience
    rationale: list[str] = []

    required_score, required_matched, required_gaps = _score_skills(
        job.required_skills, index, required=True
    )
    preferred_score, preferred_matched, preferred_gaps = _score_skills(
        job.preferred_skills, index, required=False
    )
    experience_score, meets_bar, experience_note = _score_experience(
        years, job.min_years_experience
    )
    seniority_score, seniority_note = _score_seniority(
        resume.analytics.seniority_level, job.seniority
    )
    education_score, education_note = _score_education(resume, job.education_requirement)

    if job.required_skills:
        rationale.append(
            f"Required skills: {len(required_matched)}/{len(job.required_skills)} met."
        )
    if job.preferred_skills:
        rationale.append(
            f"Preferred skills: {len(preferred_matched)}/{len(job.preferred_skills)} met."
        )
    rationale.extend((experience_note, seniority_note, education_note))

    breakdown = MatchBreakdown(
        required_skills=round(required_score, 3),
        preferred_skills=round(preferred_score, 3),
        experience=experience_score,
        seniority=seniority_score,
        education=education_score,
    )
    weighted = (
        breakdown.required_skills * active.get("required_skills", 0)
        + breakdown.preferred_skills * active.get("preferred_skills", 0)
        + breakdown.experience * active.get("experience", 0)
        + breakdown.seniority * active.get("seniority", 0)
        + breakdown.education * active.get("education", 0)
    ) / total_weight

    return MatchResult(
        score=round(weighted * 100, 1),
        breakdown=breakdown,
        matched_skills=[*required_matched, *preferred_matched],
        gaps=[*required_gaps, *preferred_gaps],
        years_experience=years,
        meets_experience_bar=meets_bar,
        rationale=rationale,
    )
