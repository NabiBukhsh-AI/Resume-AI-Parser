"""Tests for value normalization."""

from __future__ import annotations

from datetime import date

import pytest

from resume_parser.domain.enums import Proficiency
from resume_parser.domain.resume import ContactInfo, Experience, ResumeExtraction, Skill
from resume_parser.pipeline.normalization import (
    canonical_skill_name,
    normalize_date,
    normalize_email,
    normalize_phone,
    normalize_resume,
    parse_partial_date,
)


class TestDateParsing:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2021-03-15", date(2021, 3, 15)),
            ("2021-03", date(2021, 3, 1)),
            ("2021", date(2021, 1, 1)),
            ("Jan 2021", date(2021, 1, 1)),
            ("January 2021", date(2021, 1, 1)),
            ("Sept 2021", date(2021, 9, 1)),
            ("2021 March", date(2021, 3, 1)),
            ("03/2021", date(2021, 3, 1)),
            ("3-2021", date(2021, 3, 1)),
        ],
    )
    def test_supported_formats(self, value: str, expected: date) -> None:
        assert parse_partial_date(value) == expected

    @pytest.mark.parametrize("value", ["Present", "current", "NOW", "ongoing", "", None])
    def test_current_tokens_are_not_dates(self, value: str | None) -> None:
        assert parse_partial_date(value) is None

    def test_unparseable_input_returns_none(self) -> None:
        assert parse_partial_date("sometime last spring") is None

    def test_out_of_range_year_is_rejected(self) -> None:
        assert parse_partial_date("1650-01") is None

    def test_normalize_date_produces_iso_month(self) -> None:
        assert normalize_date("Jan 2021") == "2021-01"

    def test_normalize_date_preserves_unparseable_text(self) -> None:
        """Losing a date we cannot read is worse than passing it through."""
        assert normalize_date("summer 2021") == "summer 2021"


class TestContactNormalization:
    def test_email_is_lowercased_and_extracted(self) -> None:
        assert normalize_email("Contact: ADA@Example.COM") == "ada@example.com"

    def test_invalid_email_becomes_none(self) -> None:
        assert normalize_email("not an email") is None

    def test_phone_formatting_is_stripped(self) -> None:
        assert normalize_phone("+1 (415) 555-0142") == "+14155550142"

    def test_implausible_phone_is_passed_through(self) -> None:
        assert normalize_phone("12") == "12"


class TestSkillCanonicalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("js", "JavaScript"),
            ("JS", "JavaScript"),
            ("reactjs", "React"),
            ("k8s", "Kubernetes"),
            ("postgres", "PostgreSQL"),
            ("tf", "TensorFlow"),
            ("sklearn", "scikit-learn"),
        ],
    )
    def test_aliases_map_to_canonical_names(self, raw: str, expected: str) -> None:
        assert canonical_skill_name(raw) == expected

    def test_unknown_lowercase_skill_is_title_cased(self) -> None:
        assert canonical_skill_name("elixir") == "Elixir"

    def test_deliberate_casing_is_preserved(self) -> None:
        assert canonical_skill_name("PyTorch") == "PyTorch"


class TestResumeNormalization:
    def test_duplicate_skills_merge_keeping_strongest_proficiency(self) -> None:
        resume = ResumeExtraction(
            skills=[
                Skill(name="python", proficiency=Proficiency.BASIC),
                Skill(name="Python", proficiency=Proficiency.EXPERT),
                Skill(name="PYTHON"),
            ]
        )
        normalized = normalize_resume(resume)
        assert len(normalized.skills) == 1
        assert normalized.skills[0].name == "Python"
        assert normalized.skills[0].proficiency is Proficiency.EXPERT

    def test_present_end_date_marks_role_current(self) -> None:
        resume = ResumeExtraction(
            experience=[Experience(job_title="A", start_date="2021-01", end_date="Present")]
        )
        role = normalize_resume(resume).experience[0]
        assert role.is_current is True
        assert role.end_date is None

    def test_name_is_split_when_only_full_name_is_given(self) -> None:
        resume = ResumeExtraction(contact=ContactInfo(full_name="Ada King Lovelace"))
        contact = normalize_resume(resume).contact
        assert contact.first_name == "Ada"
        assert contact.last_name == "Lovelace"

    def test_full_name_is_composed_from_parts(self) -> None:
        resume = ResumeExtraction(contact=ContactInfo(first_name="Ada", last_name="Lovelace"))
        assert normalize_resume(resume).contact.full_name == "Ada Lovelace"

    def test_role_technologies_are_canonicalized(self) -> None:
        resume = ResumeExtraction(
            experience=[Experience(job_title="A", technologies=["k8s", "js"])]
        )
        assert normalize_resume(resume).experience[0].technologies == [
            "Kubernetes",
            "JavaScript",
        ]

    def test_normalization_does_not_mutate_the_input(self) -> None:
        resume = ResumeExtraction(contact=ContactInfo(email="ADA@EXAMPLE.COM"))
        normalize_resume(resume)
        assert resume.contact.email == "ADA@EXAMPLE.COM"
