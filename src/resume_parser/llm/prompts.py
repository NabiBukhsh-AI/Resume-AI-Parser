"""Prompt templates, versioned.

``PROMPT_VERSION`` is part of the cache key. Bump it whenever the wording changes, so a
deployment that improves the prompt does not keep serving results produced by the old one.

Design notes on the extraction prompt:

* The schema is enforced by constrained decoding, so the prompt does not restate field
  names. Duplicating the schema in prose wastes tokens and gives the model a second,
  possibly conflicting, specification to follow.
* It says what to do about missing data, because "invent a plausible value" is the default
  failure mode when a field must be filled.
* It does *not* ask for total years of experience. That is computed in Python from the
  dates, which is exact - the original prompt asked the model to "calculate the total by
  combining all the work experience", which double-counts overlapping roles and drifts
  between runs.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = [
    "EXTRACTION_SYSTEM_PROMPT",
    "JOB_REQUIREMENTS_SYSTEM_PROMPT",
    "PROMPT_VERSION",
    "build_extraction_user_prompt",
    "build_job_requirements_prompt",
    "build_repair_prompt",
]

PROMPT_VERSION = "2.0.0"

EXTRACTION_SYSTEM_PROMPT = """\
You are a resume parsing engine. You convert a candidate's resume into structured data \
that will be stored in an applicant tracking system.

Rules that matter more than completeness:

1. Ground every value in the document. If the resume does not state something, return \
null for that field or omit the entry entirely. Never infer an employer, a date, a \
degree, or a contact detail that is not written down.
2. Preserve the candidate's own wording for titles, company names, degrees and grades. \
Do not translate, expand abbreviations, or "tidy up" job titles.
3. Dates use ISO 8601: "YYYY-MM-DD" when a day is given, "YYYY-MM" when only month and \
year are given, "YYYY" when only a year is given. If a role is ongoing ("Present", \
"Current", "to date"), set end_date to null and is_current to true.
4. Split work history by role, not by employer. A promotion within one company is two \
entries when the resume lists two title/date pairs.
5. For skills, include what the resume evidences - named in a skills section, or clearly \
used in a role or project. Set proficiency from how the skill is actually used across the \
work history (depth, recency, seniority of the role), not from a self-rating bar or a \
progress graphic.
6. Bullet points belong in `highlights`, one entry per bullet, with the original text. \
Use `description` for prose paragraphs only.
7. If the document is not a resume, return the structure with empty sections rather than \
inventing a candidate.
"""

JOB_REQUIREMENTS_SYSTEM_PROMPT = """\
You extract structured hiring requirements from a job description.

Separate hard requirements from preferences: a skill belongs in required_skills only when \
the posting frames it as necessary ("required", "must have", "you have"), and in \
preferred_skills when it is framed as a bonus ("nice to have", "preferred", "a plus"). \
Name skills in their canonical form - "PostgreSQL", not "postgres experience". Return null \
for anything the posting does not state; do not infer a seniority level or a years-of-\
experience bar that is not written down.
"""


def build_extraction_user_prompt(resume_text: str, *, filename: str | None = None) -> str:
    """Assemble the user turn for a resume extraction call.

    The current date is included because resumes are full of relative language ("3 years
    at...", "since 2019") and a model with a training cutoff will otherwise anchor on the
    wrong "now" when judging whether a role is current.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    source = f"\nSource file: {filename}" if filename else ""
    return (
        f"Today's date is {today}. Use it when judging whether a role is ongoing."
        f"{source}\n\n"
        "Extract the structured record for the resume below.\n\n"
        "<resume>\n"
        f"{resume_text}\n"
        "</resume>"
    )


def build_job_requirements_prompt(job_text: str) -> str:
    """Assemble the user turn for job-description structuring."""
    return (
        "Extract the structured requirements from the job description below.\n\n"
        "<job_description>\n"
        f"{job_text}\n"
        "</job_description>"
    )


def build_repair_prompt(raw_response: str, error: str) -> str:
    """Ask a model to fix its own malformed JSON.

    Cheaper and far more reliable than failing the request: the content is usually correct
    and only the envelope is broken (a trailing comma, a stray prose preamble).
    """
    return (
        "Your previous response could not be parsed.\n\n"
        f"Parser error: {error}\n\n"
        "Previous response:\n"
        "<response>\n"
        f"{raw_response}\n"
        "</response>\n\n"
        "Return the same information as a single valid JSON object matching the required "
        "schema. Output only the JSON - no explanation, no code fences. Do not add, remove "
        "or alter any values; only fix the structure."
    )
