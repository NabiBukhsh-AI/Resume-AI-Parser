"""Tests for job matching."""

from __future__ import annotations

import pytest

from resume_parser.domain.enums import SeniorityLevel
from resume_parser.domain.matching import JobRequirements
from resume_parser.domain.resume import (
    Education,
    Experience,
    Resume,
    ResumeAnalytics,
    Skill,
)
from resume_parser.pipeline.matching import match_resume_to_job


def _resume(
    *,
    skills: list[str] | None = None,
    years: float = 5.0,
    seniority: SeniorityLevel = SeniorityLevel.SENIOR,
    degree: str | None = None,
) -> Resume:
    return Resume(
        skills=[Skill(name=name) for name in (skills or [])],
        education=[Education(degree=degree)] if degree else [],
        experience=[Experience(job_title="Engineer", start_date="2020-01", is_current=True)],
        analytics=ResumeAnalytics(total_years_of_experience=years, seniority_level=seniority),
    )


class TestSkillScoring:
    def test_full_match_scores_high(self) -> None:
        resume = _resume(skills=["Python", "PostgreSQL", "Docker"])
        job = JobRequirements(required_skills=["Python", "PostgreSQL", "Docker"])
        result = match_resume_to_job(resume, job)
        assert result.breakdown.required_skills == 1.0
        assert result.score > 90

    def test_missing_required_skills_become_gaps(self) -> None:
        resume = _resume(skills=["Python"])
        job = JobRequirements(required_skills=["Python", "Rust", "Elixir"])
        result = match_resume_to_job(resume, job)
        assert result.breakdown.required_skills == pytest.approx(1 / 3, abs=0.01)
        assert {gap.skill for gap in result.gaps} == {"Rust", "Elixir"}
        assert all(gap.required for gap in result.gaps)

    def test_aliases_are_matched(self) -> None:
        """A resume saying 'k8s' satisfies a job asking for 'Kubernetes'."""
        resume = _resume(skills=["k8s", "js"])
        job = JobRequirements(required_skills=["Kubernetes", "JavaScript"])
        assert match_resume_to_job(resume, job).breakdown.required_skills == 1.0

    def test_skills_from_role_technologies_count(self) -> None:
        resume = Resume(
            experience=[
                Experience(
                    job_title="Engineer",
                    technologies=["Terraform"],
                    start_date="2020-01",
                    is_current=True,
                )
            ],
            analytics=ResumeAnalytics(total_years_of_experience=5.0),
        )
        job = JobRequirements(required_skills=["Terraform"])
        assert match_resume_to_job(resume, job).breakdown.required_skills == 1.0

    def test_no_requirements_does_not_penalise(self) -> None:
        result = match_resume_to_job(_resume(skills=["Python"]), JobRequirements())
        assert result.breakdown.required_skills == 1.0

    def test_near_miss_is_reported_as_closest_match_not_a_hit(self) -> None:
        resume = _resume(skills=["PostgreSQL"])
        job = JobRequirements(required_skills=["Cassandra"])
        result = match_resume_to_job(resume, job)
        assert result.breakdown.required_skills == 0.0


class TestExperienceScoring:
    def test_meeting_the_bar_scores_full(self) -> None:
        result = match_resume_to_job(_resume(years=7.0), JobRequirements(min_years_experience=5))
        assert result.breakdown.experience == 1.0
        assert result.meets_experience_bar is True

    def test_falling_short_gets_partial_credit(self) -> None:
        result = match_resume_to_job(_resume(years=4.0), JobRequirements(min_years_experience=5))
        assert result.breakdown.experience == pytest.approx(0.8, abs=0.01)
        assert result.meets_experience_bar is False

    def test_no_stated_minimum_is_not_scored(self) -> None:
        assert (
            match_resume_to_job(_resume(years=1.0), JobRequirements()).breakdown.experience == 1.0
        )


class TestSeniorityScoring:
    def test_exact_level_scores_full(self) -> None:
        result = match_resume_to_job(
            _resume(seniority=SeniorityLevel.SENIOR),
            JobRequirements(seniority=SeniorityLevel.SENIOR),
        )
        assert result.breakdown.seniority == 1.0

    def test_distance_reduces_the_score(self) -> None:
        result = match_resume_to_job(
            _resume(seniority=SeniorityLevel.JUNIOR),
            JobRequirements(seniority=SeniorityLevel.PRINCIPAL),
        )
        assert result.breakdown.seniority == 0.0


class TestEducationScoring:
    def test_meeting_the_degree_requirement(self) -> None:
        result = match_resume_to_job(
            _resume(degree="MSc Computer Science"),
            JobRequirements(education_requirement="Bachelor's degree required"),
        )
        assert result.breakdown.education == 1.0

    def test_no_degree_when_one_is_required(self) -> None:
        result = match_resume_to_job(
            _resume(), JobRequirements(education_requirement="PhD required")
        )
        assert result.breakdown.education == 0.0

    def test_lower_degree_gets_partial_credit(self) -> None:
        result = match_resume_to_job(
            _resume(degree="BSc Computer Science"),
            JobRequirements(education_requirement="Master's degree required"),
        )
        assert result.breakdown.education == 0.5


class TestOverallScore:
    def test_score_is_bounded_and_explained(self) -> None:
        resume = _resume(skills=["Python", "AWS"], years=6.0, degree="BSc Computing")
        job = JobRequirements(
            required_skills=["Python", "AWS", "Kubernetes"],
            preferred_skills=["Terraform"],
            min_years_experience=5,
            seniority=SeniorityLevel.SENIOR,
            education_requirement="Bachelor's degree",
        )
        result = match_resume_to_job(resume, job)
        assert 0 <= result.score <= 100
        assert result.rationale
        assert result.years_experience == 6.0

    def test_scoring_is_deterministic(self) -> None:
        """The same inputs must always produce the same score - it is an auditable signal."""
        resume = _resume(skills=["Python", "Docker"])
        job = JobRequirements(required_skills=["Python", "Go"], min_years_experience=3)
        scores = {match_resume_to_job(resume, job).score for _ in range(5)}
        assert len(scores) == 1

    def test_custom_weights_are_renormalized(self) -> None:
        resume = _resume(skills=["Python"])
        job = JobRequirements(required_skills=["Python", "Rust"])
        weighted = match_resume_to_job(
            resume, job, weights={"required_skills": 10, "experience": 1}
        )
        assert 0 <= weighted.score <= 100
