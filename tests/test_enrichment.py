"""Tests for the deterministic analytics layer.

These cover the single most important correctness claim in the project: that total years of
experience is exact and does not double-count concurrent roles.
"""

from __future__ import annotations

from datetime import date

import pytest

from resume_parser.domain.enums import SeniorityLevel
from resume_parser.domain.resume import Experience, ResumeExtraction
from resume_parser.pipeline.enrichment import (
    build_analytics,
    infer_seniority,
    merge_intervals,
    total_experience_years,
)

TODAY = date(2026, 1, 1)


def _resume(*roles: Experience) -> ResumeExtraction:
    return ResumeExtraction(experience=list(roles))


class TestMergeIntervals:
    def test_disjoint_intervals_are_preserved(self) -> None:
        merged = merge_intervals(
            [(date(2018, 1, 1), date(2019, 1, 1)), (date(2020, 1, 1), date(2021, 1, 1))]
        )
        assert len(merged) == 2

    def test_overlapping_intervals_collapse(self) -> None:
        merged = merge_intervals(
            [(date(2018, 1, 1), date(2020, 1, 1)), (date(2019, 1, 1), date(2021, 1, 1))]
        )
        assert merged == [(date(2018, 1, 1), date(2021, 1, 1))]

    def test_contained_interval_is_absorbed(self) -> None:
        merged = merge_intervals(
            [(date(2018, 1, 1), date(2024, 1, 1)), (date(2019, 1, 1), date(2020, 1, 1))]
        )
        assert merged == [(date(2018, 1, 1), date(2024, 1, 1))]

    def test_empty_input(self) -> None:
        assert merge_intervals([]) == []


class TestTotalExperience:
    def test_single_role(self) -> None:
        resume = _resume(Experience(job_title="Engineer", start_date="2020-01", end_date="2023-01"))
        assert total_experience_years(resume, today=TODAY) == pytest.approx(3.0, abs=0.1)

    def test_concurrent_roles_are_not_double_counted(self) -> None:
        """The bug the original prompt-based approach produced: 2+2 reported as 4."""
        resume = _resume(
            Experience(job_title="Engineer", start_date="2020-01", end_date="2022-01"),
            Experience(job_title="Consultant", start_date="2020-01", end_date="2022-01"),
        )
        assert total_experience_years(resume, today=TODAY) == pytest.approx(2.0, abs=0.1)

    def test_partially_overlapping_roles(self) -> None:
        resume = _resume(
            Experience(job_title="A", start_date="2018-01", end_date="2020-01"),
            Experience(job_title="B", start_date="2019-01", end_date="2021-01"),
        )
        assert total_experience_years(resume, today=TODAY) == pytest.approx(3.0, abs=0.1)

    def test_current_role_runs_to_today(self) -> None:
        resume = _resume(Experience(job_title="A", start_date="2024-01", is_current=True))
        assert total_experience_years(resume, today=TODAY) == pytest.approx(2.0, abs=0.1)

    def test_undated_roles_contribute_nothing(self) -> None:
        resume = _resume(Experience(job_title="Mystery role"))
        assert total_experience_years(resume, today=TODAY) == 0.0

    def test_reversed_dates_are_repaired(self) -> None:
        resume = _resume(Experience(job_title="A", start_date="2022-01", end_date="2020-01"))
        assert total_experience_years(resume, today=TODAY) == pytest.approx(2.0, abs=0.1)

    def test_future_end_date_is_clamped_to_today(self) -> None:
        resume = _resume(Experience(job_title="A", start_date="2024-01", end_date="2030-01"))
        assert total_experience_years(resume, today=TODAY) == pytest.approx(2.0, abs=0.1)


class TestSeniority:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Senior Software Engineer", SeniorityLevel.SENIOR),
            ("Engineering Manager", SeniorityLevel.LEAD),
            ("Principal Architect", SeniorityLevel.PRINCIPAL),
            ("Chief Technology Officer", SeniorityLevel.EXECUTIVE),
            ("Software Engineering Intern", SeniorityLevel.INTERN),
            ("Junior Developer", SeniorityLevel.JUNIOR),
        ],
    )
    def test_title_signals_win(self, title: str, expected: SeniorityLevel) -> None:
        resume = _resume(Experience(job_title=title, start_date="2024-01", is_current=True))
        assert infer_seniority(resume, 1.0) is expected

    def test_tenure_decides_when_titles_are_neutral(self) -> None:
        resume = _resume(Experience(job_title="Software Engineer", start_date="2018-01"))
        assert infer_seniority(resume, 8.0) is SeniorityLevel.SENIOR

    def test_empty_resume_is_unknown(self) -> None:
        assert infer_seniority(ResumeExtraction(), 0.0) is SeniorityLevel.UNKNOWN


class TestAnalytics:
    def test_career_gap_is_measured(self) -> None:
        resume = _resume(
            Experience(job_title="A", start_date="2016-01", end_date="2018-01"),
            Experience(job_title="B", start_date="2019-01", end_date="2021-01"),
        )
        analytics = build_analytics(resume, today=TODAY)
        assert analytics.career_gaps_months == pytest.approx(12, abs=1)

    def test_continuous_history_has_no_gap(self) -> None:
        resume = _resume(
            Experience(job_title="A", start_date="2018-01", end_date="2020-01"),
            Experience(job_title="B", start_date="2020-01", end_date="2022-01"),
        )
        assert build_analytics(resume, today=TODAY).career_gaps_months == 0

    def test_completeness_reports_missing_sections(self) -> None:
        analytics = build_analytics(ResumeExtraction(), today=TODAY)
        assert analytics.completeness_score == 0.0
        assert "experience" in analytics.missing_sections
        assert "contact.email" in analytics.missing_sections

    def test_current_role_is_identified(self) -> None:
        resume = _resume(
            Experience(job_title="Now", company="NowCo", start_date="2024-01", is_current=True),
            Experience(
                job_title="Before", company="OldCo", start_date="2020-01", end_date="2023-12"
            ),
        )
        analytics = build_analytics(resume, today=TODAY)
        assert analytics.current_position == "Now"
        assert analytics.current_company == "NowCo"
        assert analytics.companies == ["NowCo", "OldCo"]

    def test_average_tenure_ignores_current_role(self) -> None:
        resume = _resume(
            Experience(job_title="A", start_date="2018-01", end_date="2020-01"),
            Experience(job_title="B", start_date="2020-01", is_current=True),
        )
        analytics = build_analytics(resume, today=TODAY)
        assert analytics.average_tenure_years == pytest.approx(2.0, abs=0.1)
