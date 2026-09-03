"""Streamlit interface.

A thin view over :class:`ResumeParsingService`. It contains no parsing logic, no validation
rules and no provider knowledge - if a behaviour needs changing, it changes in the service
and all three surfaces (API, CLI, UI) pick it up.

One security note on the original: it rendered an "API Key" box, compared what the user
typed against the server's own ``SECURE_API_KEY``, and refused to parse on a mismatch. That
protects nothing - anyone who can open the page is already running with the server's
credentials - while teaching users to paste a shared secret into a web form. Access control
belongs at the API and at the deployment's ingress, so the box is gone.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import streamlit as st

from resume_parser import __version__
from resume_parser.domain.results import ParseResult
from resume_parser.exceptions import ResumeParserError
from resume_parser.pipeline.parser import ResumeParsingService
from resume_parser.settings import Settings, get_settings

_ACCEPTED = ["pdf", "docx", "txt", "md"]


@st.cache_resource(show_spinner=False)
def _service(_settings: Settings) -> ResumeParsingService:
    """Build the parsing service once per Streamlit session worker.

    Cached because it holds HTTP connection pools and a compiled JSON Schema; rebuilding
    it on every rerun would discard the cache and the pools on each widget interaction.
    """
    return ResumeParsingService(_settings)


def _run(coro: Any) -> Any:
    """Run a coroutine from Streamlit's synchronous script thread."""
    return asyncio.run(coro)


def _render_header(settings: Settings) -> None:
    """Title, description and the provider status line."""
    st.title("Resume AI Parser")
    st.caption(
        "Upload a resume to extract a structured, validated record. "
        "Experience totals are computed from the extracted dates, not guessed by the model."
    )
    usable = [spec.label for spec in settings.configured_models()]
    if not usable:
        st.error(
            "No LLM provider is configured. Set `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` "
            "or `OPENAI_API_KEY` in your environment or `.env`, then restart."
        )
        st.stop()
    st.sidebar.success(f"Model chain: {' -> '.join(usable)}")


def _render_metrics(result: ParseResult) -> None:
    """Top-line numbers for a parsed resume."""
    analytics = result.resume.analytics
    columns = st.columns(4)
    columns[0].metric("Experience", f"{analytics.total_years_of_experience} yrs")
    columns[1].metric("Seniority", analytics.seniority_level.value.replace("_", " ").title())
    columns[2].metric("Skills", len(result.resume.skills))
    columns[3].metric("Completeness", f"{analytics.completeness_score:.0%}")


def _render_overview(result: ParseResult) -> None:
    """Contact block, summary and derived signals."""
    resume = result.resume
    contact = resume.contact
    left, right = st.columns([2, 1])

    with left:
        st.subheader(contact.full_name or "Unknown candidate")
        if resume.headline:
            st.markdown(f"**{resume.headline}**")
        details = [
            f"Email: {contact.email}" if contact.email else None,
            f"Phone: {contact.phone}" if contact.phone else None,
            f"Location: {contact.location}" if contact.location else None,
            f"[LinkedIn]({contact.links.linkedin})" if contact.links.linkedin else None,
            f"[GitHub]({contact.links.github})" if contact.links.github else None,
        ]
        st.markdown(" · ".join(item for item in details if item) or "_No contact details found._")
        if resume.summary:
            st.markdown(resume.summary)

    with right:
        st.markdown("**Top skills**")
        st.markdown(
            "\n".join(f"- {skill}" for skill in resume.analytics.top_skills[:10])
            or "_None identified._"
        )


def _render_experience(result: ParseResult) -> None:
    """Work history, most recent first."""
    if not result.resume.experience:
        st.info("No work experience was extracted from this document.")
        return
    for role in result.resume.experience:
        period = (
            f"{role.start_date or '?'} - {'Present' if role.is_current else role.end_date or '?'}"
        )
        header = f"{role.job_title or 'Role'} · {role.company or 'Unknown'}  ({period})"
        with st.expander(header, expanded=False):
            if role.location:
                st.caption(role.location)
            if role.description:
                st.write(role.description)
            for highlight in role.highlights:
                st.markdown(f"- {highlight}")
            if role.technologies:
                st.caption("Tech: " + ", ".join(role.technologies))


def _render_skills(result: ParseResult) -> None:
    """Skills grouped by category."""
    if not result.resume.skills:
        st.info("No skills were extracted.")
        return
    grouped: dict[str, list[str]] = {}
    for skill in result.resume.skills:
        label = skill.name + (f" ({skill.proficiency.value})" if skill.proficiency else "")
        grouped.setdefault(skill.category.value.replace("_", " ").title(), []).append(label)
    for category, names in sorted(grouped.items()):
        st.markdown(f"**{category}**")
        st.markdown(", ".join(sorted(names)))


def _render_usage(result: ParseResult) -> None:
    """Model, cost and timing - the numbers an operator cares about."""
    usage = result.usage
    columns = st.columns(4)
    columns[0].metric("Model", usage.model)
    columns[1].metric("Tokens", f"{usage.tokens.total_tokens:,}")
    columns[2].metric(
        "Cost", f"${usage.estimated_cost_usd:.4f}" if usage.estimated_cost_usd else "n/a"
    )
    columns[3].metric("Latency", f"{usage.latency_ms} ms")
    flags = [
        "served from cache" if usage.cached else None,
        "fallback model used" if usage.fallback_used else None,
        f"{usage.attempts} attempts" if usage.attempts > 1 else None,
    ]
    if active := [flag for flag in flags if flag]:
        st.caption(" · ".join(active))


def _render_match(service: ResumeParsingService, result: ParseResult) -> None:
    """Optional job-matching panel."""
    st.markdown("Paste a job description to score this candidate against it.")
    job_text = st.text_area("Job description", height=200, label_visibility="collapsed")
    if not st.button("Score match", type="primary", disabled=not job_text.strip()):
        return

    with st.spinner("Scoring..."):
        try:
            requirements = _run(service.extract_job_requirements(job_text))
            match = service.match(result.resume, requirements)
        except ResumeParserError as exc:
            st.error(exc.message)
            return

    st.metric("Match score", f"{match.score}/100")
    st.progress(min(1.0, match.score / 100))

    left, right = st.columns(2)
    with left:
        st.markdown("**Breakdown**")
        for field, value in match.breakdown.model_dump().items():
            st.markdown(f"- {field.replace('_', ' ').title()}: {value:.0%}")
    with right:
        if match.matched_skills:
            st.markdown("**Matched**")
            st.success(", ".join(match.matched_skills))
        required_gaps = [gap.skill for gap in match.gaps if gap.required]
        preferred_gaps = [gap.skill for gap in match.gaps if not gap.required]
        if required_gaps:
            st.markdown("**Missing (required)**")
            st.error(", ".join(required_gaps))
        if preferred_gaps:
            st.markdown("**Missing (preferred)**")
            st.warning(", ".join(preferred_gaps))

    for note in match.rationale:
        st.caption(f"- {note}")


def render() -> None:
    """Compose and run the page."""
    st.set_page_config(
        page_title="Resume AI Parser", page_icon="📄", layout="wide", initial_sidebar_state="auto"
    )
    settings = get_settings()
    _render_header(settings)
    service = _service(settings)

    st.sidebar.caption(f"Version {__version__} · {settings.environment}")
    st.sidebar.metric("Cache hit rate", f"{service.cache.stats['hit_rate']:.0%}")

    uploaded = st.file_uploader(
        "Resume file",
        type=_ACCEPTED,
        help=(
            "PDF, DOCX, TXT or Markdown, up to "
            f"{settings.extraction.max_file_size // 1_048_576} MiB."
        ),
    )
    if uploaded is None:
        st.info("Upload a resume to begin.")
        return

    if not st.button("Parse resume", type="primary"):
        return

    with st.spinner("Parsing..."):
        try:
            result = _run(service.parse(uploaded.getvalue(), filename=uploaded.name))
        except ResumeParserError as exc:
            st.error(f"{exc.code}: {exc.message}")
            return

    st.success(f"Parsed {uploaded.name}")
    for warning in result.warnings:
        st.warning(warning)

    _render_metrics(result)
    tabs = st.tabs(["Overview", "Experience", "Skills", "Job match", "JSON", "Usage"])
    with tabs[0]:
        _render_overview(result)
    with tabs[1]:
        _render_experience(result)
    with tabs[2]:
        _render_skills(result)
    with tabs[3]:
        _render_match(service, result)
    with tabs[4]:
        payload = result.model_dump(mode="json")
        st.json(payload)
        st.download_button(
            "Download JSON",
            data=json.dumps(payload, indent=2),
            file_name=f"{uploaded.name.rsplit('.', 1)[0]}.json",
            mime="application/json",
        )
    with tabs[5]:
        _render_usage(result)


render()
