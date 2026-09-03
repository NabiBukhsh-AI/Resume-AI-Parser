"""The resume domain model - the single source of truth for this project.

These Pydantic models serve four jobs at once, which is the whole point of defining them
once and deriving everything else:

1. the JSON Schema handed to the LLM for constrained decoding,
2. validation of whatever the model returns,
3. the FastAPI response model (and therefore the OpenAPI contract), and
4. the type-checked object the normalization and matching pipeline operates on.

``ResumeExtraction`` is the LLM-facing surface: everything that can be *read off* the
document. ``Resume`` extends it with ``analytics``, which the pipeline computes
deterministically in Python. Never ask a language model for arithmetic you can do
yourself - see :mod:`resume_parser.pipeline.enrichment`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from resume_parser.domain.enums import (
    EmploymentType,
    LanguageFluency,
    Proficiency,
    SeniorityLevel,
    SkillCategory,
)

__all__ = [
    "Certification",
    "ContactInfo",
    "Education",
    "Experience",
    "LanguageSkill",
    "Project",
    "Resume",
    "ResumeAnalytics",
    "ResumeExtraction",
    "Skill",
    "WebPresence",
]


class _Base(BaseModel):
    """Shared configuration for every domain model."""

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls_to_defaults(cls, data: Any) -> Any:
        """Treat an explicit ``null`` as "not provided" for fields that have a default.

        Models occasionally answer ``null`` for a list or a nested object even though the
        schema says otherwise. Rejecting the whole document over that would throw away a
        good extraction for a cosmetic defect, so a null on a field that already has a
        default is replaced by that default.

        The null is *substituted* rather than dropped: ``validate_assignment`` re-runs this
        validator over the model's own ``__dict__``, and removing keys there would delete
        the attributes entirely.
        """
        if not isinstance(data, dict):
            return data
        patched = dict(data)
        for key, value in data.items():
            if value is not None:
                continue
            field = cls.model_fields.get(key)
            if field is not None and not field.is_required():
                patched[key] = field.get_default(call_default_factory=True)
        return patched


# --------------------------------------------------------------------------- people


class WebPresence(_Base):
    """Links a candidate publishes on their resume."""

    linkedin: str | None = Field(default=None, description="LinkedIn profile URL.")
    github: str | None = Field(default=None, description="GitHub profile URL.")
    portfolio: str | None = Field(default=None, description="Personal site or portfolio URL.")
    other: list[str] = Field(
        default_factory=list, description="Any other profile or publication URLs."
    )


class ContactInfo(_Base):
    """Identity and reachability."""

    full_name: str | None = Field(default=None, description="Candidate's full name as written.")
    first_name: str | None = Field(default=None, description="Given name.")
    last_name: str | None = Field(default=None, description="Family name.")
    email: str | None = Field(default=None, description="Primary email address.")
    phone: str | None = Field(default=None, description="Primary phone number.")
    location: str | None = Field(default=None, description="City, region and/or country.")
    links: WebPresence = Field(
        default_factory=WebPresence, description="Profile and portfolio links."
    )


# ------------------------------------------------------------------------- sections


class Experience(_Base):
    """One role in the candidate's work history."""

    job_title: str | None = Field(default=None, description="Role title held.")
    company: str | None = Field(default=None, description="Employer or client name.")
    employment_type: EmploymentType | None = Field(
        default=None, description="Engagement type, if stated or clearly implied."
    )
    location: str | None = Field(default=None, description="Where the role was based.")
    start_date: str | None = Field(
        default=None,
        description=(
            "Start date as ISO 8601 'YYYY-MM' or 'YYYY-MM-DD'. Use 'YYYY' if only a year is given."
        ),
    )
    end_date: str | None = Field(
        default=None,
        description="End date in the same format. Leave null when the role is ongoing.",
    )
    is_current: bool = Field(
        default=False, description="True when this is an ongoing role ('Present', 'Current')."
    )
    description: str | None = Field(default=None, description="Prose summary of the role.")
    highlights: list[str] = Field(
        default_factory=list, description="Individual achievement or responsibility bullets."
    )
    technologies: list[str] = Field(
        default_factory=list, description="Tools and technologies named for this role."
    )


class Education(_Base):
    """One academic credential."""

    degree: str | None = Field(default=None, description="Degree or qualification earned.")
    field_of_study: str | None = Field(default=None, description="Major, discipline or programme.")
    institution: str | None = Field(default=None, description="School, college or university.")
    location: str | None = Field(default=None, description="Where the institution is based.")
    start_date: str | None = Field(default=None, description="Start date, ISO 8601 partial.")
    end_date: str | None = Field(
        default=None, description="Completion or expected completion date, ISO 8601 partial."
    )
    grade: str | None = Field(default=None, description="GPA, class or grade exactly as written.")
    description: str | None = Field(default=None, description="Thesis, honours or coursework.")


class Skill(_Base):
    """A single capability claimed by, or evidenced in, the resume."""

    name: str = Field(description="Canonical skill name, e.g. 'PostgreSQL'.")
    category: SkillCategory = Field(
        default=SkillCategory.OTHER, description="Coarse bucket this skill belongs to."
    )
    proficiency: Proficiency | None = Field(
        default=None,
        description=(
            "Command of the skill, inferred from how it is used across the work history "
            "rather than from a self-rating bar."
        ),
    )
    years_of_experience: float | None = Field(
        default=None,
        ge=0,
        le=70,
        description="Approximate years applying this skill, if it can be inferred.",
    )


class Certification(_Base):
    """A professional certification or licence."""

    name: str = Field(description="Certification name.")
    issuer: str | None = Field(default=None, description="Awarding body.")
    issue_date: str | None = Field(default=None, description="Issue date, ISO 8601 partial.")
    expiry_date: str | None = Field(default=None, description="Expiry date, ISO 8601 partial.")
    credential_id: str | None = Field(default=None, description="Credential or licence number.")


class Project(_Base):
    """A portfolio, open-source or academic project."""

    name: str = Field(description="Project name.")
    description: str | None = Field(default=None, description="What the project does.")
    role: str | None = Field(default=None, description="The candidate's role on the project.")
    url: str | None = Field(default=None, description="Repository or demo URL.")
    technologies: list[str] = Field(default_factory=list, description="Stack used.")
    start_date: str | None = Field(default=None, description="Start date, ISO 8601 partial.")
    end_date: str | None = Field(default=None, description="End date, ISO 8601 partial.")


class LanguageSkill(_Base):
    """A spoken or written human language."""

    name: str = Field(description="Language name in English, e.g. 'Spanish'.")
    fluency: LanguageFluency | None = Field(default=None, description="Stated fluency level.")


# ------------------------------------------------------------------- LLM-facing doc


class ResumeExtraction(_Base):
    """Everything an LLM is asked to read off a resume.

    Nothing here is computed: every field should be traceable to text in the document.
    Derived quantities live on :class:`ResumeAnalytics` instead.
    """

    contact: ContactInfo = Field(
        default_factory=ContactInfo, description="Identity and contact details."
    )
    headline: str | None = Field(
        default=None, description="Current or most recent job title, e.g. 'Senior Data Engineer'."
    )
    summary: str | None = Field(
        default=None,
        description=(
            "The candidate's professional summary. If the resume has no summary section, "
            "write a factual two-to-three sentence one grounded only in the document."
        ),
    )
    experience: list[Experience] = Field(
        default_factory=list, description="Work history, most recent first."
    )
    education: list[Education] = Field(
        default_factory=list, description="Academic history, most recent first."
    )
    skills: list[Skill] = Field(default_factory=list, description="Skills evidenced in the resume.")
    certifications: list[Certification] = Field(
        default_factory=list, description="Certifications and licences."
    )
    projects: list[Project] = Field(default_factory=list, description="Notable projects.")
    languages: list[LanguageSkill] = Field(
        default_factory=list, description="Human languages spoken."
    )
    awards: list[str] = Field(default_factory=list, description="Awards and honours.")
    publications: list[str] = Field(default_factory=list, description="Papers and publications.")


# ------------------------------------------------------------------ derived signals


class ResumeAnalytics(_Base):
    """Signals computed in Python from a validated :class:`ResumeExtraction`.

    Keeping these out of the LLM's hands is a deliberate accuracy decision: date
    arithmetic and completeness scoring are exact operations, and models are unreliable
    at both. See :func:`resume_parser.pipeline.enrichment.build_analytics`.
    """

    total_years_of_experience: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Total professional experience in years, computed by merging overlapping "
            "employment intervals so concurrent roles are never double-counted."
        ),
    )
    seniority_level: SeniorityLevel = Field(
        default=SeniorityLevel.UNKNOWN, description="Career stage inferred from titles and tenure."
    )
    current_position: str | None = Field(
        default=None, description="Title of the role flagged as current, if any."
    )
    current_company: str | None = Field(default=None, description="Employer for the current role.")
    companies: list[str] = Field(
        default_factory=list, description="Distinct employers, most recent first."
    )
    top_skills: list[str] = Field(
        default_factory=list, description="Highest-signal skills, ranked by evidence."
    )
    average_tenure_years: float | None = Field(
        default=None, ge=0, description="Mean length of completed roles, in years."
    )
    career_gaps_months: int = Field(
        default=0,
        ge=0,
        description="Total months not covered by any role, between the first and last role.",
    )
    completeness_score: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description="Fraction of high-value sections that were populated, 0-1.",
    )
    missing_sections: list[str] = Field(
        default_factory=list, description="High-value sections that came back empty."
    )


class Resume(ResumeExtraction):
    """A fully processed resume: what was extracted, plus what we derived from it."""

    analytics: ResumeAnalytics = Field(
        default_factory=ResumeAnalytics, description="Deterministically computed signals."
    )
