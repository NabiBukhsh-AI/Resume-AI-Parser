"""Controlled vocabularies shared by the domain models.

Enumerations are deliberately small and stable: they become ``enum`` constraints in
the JSON Schema handed to the LLM, which measurably reduces free-text drift.
"""

from __future__ import annotations

from enum import StrEnum


class Proficiency(StrEnum):
    """Self-reported or inferred command of a skill."""

    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class SkillCategory(StrEnum):
    """Coarse bucket used for grouping skills in UIs and match reports."""

    PROGRAMMING_LANGUAGE = "programming_language"
    FRAMEWORK = "framework"
    DATABASE = "database"
    CLOUD = "cloud"
    TOOL = "tool"
    METHODOLOGY = "methodology"
    SOFT_SKILL = "soft_skill"
    DOMAIN = "domain"
    OTHER = "other"


class EmploymentType(StrEnum):
    """Engagement type for a work-history entry."""

    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    FREELANCE = "freelance"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    VOLUNTEER = "volunteer"
    OTHER = "other"


class SeniorityLevel(StrEnum):
    """Career stage, derived from titles and cumulative experience."""

    INTERN = "intern"
    ENTRY = "entry"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    PRINCIPAL = "principal"
    EXECUTIVE = "executive"
    UNKNOWN = "unknown"


class LanguageFluency(StrEnum):
    """CEFR-inspired fluency bands for spoken/written languages."""

    ELEMENTARY = "elementary"
    LIMITED_WORKING = "limited_working"
    PROFESSIONAL_WORKING = "professional_working"
    FULL_PROFESSIONAL = "full_professional"
    NATIVE = "native"


class DocumentFormat(StrEnum):
    """Input document formats the extraction layer understands."""

    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MARKDOWN = "md"
    HTML = "html"
    RTF = "rtf"
