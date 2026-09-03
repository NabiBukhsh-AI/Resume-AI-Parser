"""Models for job-description matching.

The matcher answers a question recruiters actually have - *how well does this candidate
fit this role, and what is missing?* - and answers it with an auditable breakdown rather
than a single opaque number.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from resume_parser.domain.enums import SeniorityLevel

__all__ = ["JobRequirements", "MatchBreakdown", "MatchResult", "SkillGap"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class JobRequirements(_Base):
    """A structured view of a job description.

    Populated either by the caller directly or by an LLM pass over free-text JD copy.
    """

    title: str | None = Field(default=None, description="Job title.")
    seniority: SeniorityLevel | None = Field(default=None, description="Target career stage.")
    required_skills: list[str] = Field(
        default_factory=list, description="Skills the role treats as mandatory."
    )
    preferred_skills: list[str] = Field(
        default_factory=list, description="Skills listed as nice-to-have."
    )
    min_years_experience: float | None = Field(
        default=None, ge=0, le=70, description="Minimum years of experience requested."
    )
    education_requirement: str | None = Field(
        default=None, description="Required degree or field of study, if stated."
    )
    responsibilities: list[str] = Field(
        default_factory=list, description="Core duties named in the posting."
    )


class SkillGap(_Base):
    """One required skill the candidate does not evidence."""

    skill: str = Field(description="The requirement that was not met.")
    required: bool = Field(description="True when mandatory, False when preferred.")
    closest_match: str | None = Field(
        default=None, description="Nearest skill the candidate does have, if any."
    )


class MatchBreakdown(_Base):
    """Per-dimension sub-scores, each normalized to 0-1."""

    required_skills: float = Field(default=0.0, ge=0, le=1)
    preferred_skills: float = Field(default=0.0, ge=0, le=1)
    experience: float = Field(default=0.0, ge=0, le=1)
    seniority: float = Field(default=0.0, ge=0, le=1)
    education: float = Field(default=0.0, ge=0, le=1)


class MatchResult(_Base):
    """The full, explainable outcome of scoring one resume against one job."""

    score: float = Field(ge=0, le=100, description="Overall fit, 0-100.")
    breakdown: MatchBreakdown = Field(description="Weighted components behind the score.")
    matched_skills: list[str] = Field(
        default_factory=list, description="Requirements the candidate demonstrably meets."
    )
    gaps: list[SkillGap] = Field(default_factory=list, description="Unmet requirements.")
    years_experience: float = Field(
        default=0.0, ge=0, description="Candidate's computed total experience."
    )
    meets_experience_bar: bool = Field(
        default=True, description="Whether the minimum-years requirement is satisfied."
    )
    rationale: list[str] = Field(
        default_factory=list, description="Human-readable notes explaining each sub-score."
    )
