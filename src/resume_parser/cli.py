"""Command-line interface.

A first-class CLI is what makes this usable in the places a web UI cannot reach: batch
imports, cron jobs, CI checks, and quick local evaluation of a prompt change. Every command
goes through the same :class:`ResumeParsingService` the API uses, so behaviour cannot drift
between the two surfaces.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from resume_parser import __version__
from resume_parser.domain.results import ParseResult
from resume_parser.exceptions import ResumeParserError
from resume_parser.observability.logging import configure_logging
from resume_parser.pipeline.parser import BatchItem, ResumeParsingService
from resume_parser.settings import Settings, get_settings

__all__ = ["app"]

app = typer.Typer(
    name="resume-parser",
    help="Parse resumes into structured data, and score them against jobs.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

_SUPPORTED = {".pdf", ".docx", ".txt", ".md"}


def _bootstrap(verbose: bool) -> Settings:
    """Load settings and configure logging for a CLI run."""
    settings = get_settings()
    configure_logging(
        level="DEBUG" if verbose else settings.observability.log_level,
        json_logs=settings.observability.json_logs,
        redact_pii=settings.observability.redact_pii,
        force=True,
    )
    return settings


def _fail(message: str) -> None:
    """Print an error and exit non-zero."""
    err_console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code=1)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"resume-parser {__version__}")
        raise typer.Exit


@app.callback()
def main(
    _version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show the version."
        ),
    ] = False,
) -> None:
    """Resume AI Parser."""


# --------------------------------------------------------------------------- parse


@app.command()
def parse(
    path: Annotated[Path, typer.Argument(help="Resume file to parse.", exists=True)],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write JSON here instead of stdout.")
    ] = None,
    summary: Annotated[
        bool, typer.Option("--summary/--json", help="Print a readable summary instead of JSON.")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
) -> None:
    """Parse a single resume."""
    settings = _bootstrap(verbose)

    async def run() -> ParseResult:
        service = ResumeParsingService(settings)
        try:
            return await service.parse(path.read_bytes(), filename=path.name)
        finally:
            await service.aclose()

    try:
        result = asyncio.run(run())
    except ResumeParserError as exc:
        _fail(exc.message)
        return

    if summary:
        _print_summary(result)
    payload = result.model_dump_json(indent=2)
    if output:
        output.write_text(payload, encoding="utf-8")
        console.print(f"[green]Wrote[/green] {output}")
    elif not summary:
        console.print_json(payload)


@app.command()
def batch(
    directory: Annotated[
        Path, typer.Argument(help="Directory of resumes.", exists=True, file_okay=False)
    ],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Directory for the JSON results.")
    ] = Path("parsed"),
    recursive: Annotated[bool, typer.Option("--recursive", "-r", help="Recurse.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
) -> None:
    """Parse every resume in a directory, writing one JSON file per document."""
    settings = _bootstrap(verbose)
    pattern = "**/*" if recursive else "*"
    files = sorted(
        candidate
        for candidate in directory.glob(pattern)
        if candidate.is_file() and candidate.suffix.lower() in _SUPPORTED
    )
    if not files:
        _fail(f"No supported documents found in {directory}.")

    console.print(f"Parsing [bold]{len(files)}[/bold] documents...")
    items = [BatchItem(data=path.read_bytes(), filename=path.name) for path in files]

    async def run() -> list[ParseResult | Exception]:
        service = ResumeParsingService(settings)
        try:
            return await service.parse_batch(items)
        finally:
            await service.aclose()

    outcomes = asyncio.run(run())
    output.mkdir(parents=True, exist_ok=True)

    table = Table(title="Batch results", show_lines=False)
    table.add_column("File")
    table.add_column("Status")
    table.add_column("Years", justify="right")
    table.add_column("Skills", justify="right")
    table.add_column("Cost", justify="right")

    failures = 0
    for path, outcome in zip(files, outcomes, strict=True):
        if isinstance(outcome, ParseResult):
            destination = output / f"{path.stem}.json"
            destination.write_text(outcome.model_dump_json(indent=2), encoding="utf-8")
            cost = outcome.usage.estimated_cost_usd
            table.add_row(
                path.name,
                "[green]ok[/green]",
                str(outcome.resume.analytics.total_years_of_experience),
                str(len(outcome.resume.skills)),
                f"${cost:.4f}" if cost is not None else "-",
            )
        else:
            failures += 1
            reason = getattr(outcome, "message", str(outcome))
            table.add_row(path.name, f"[red]failed[/red] {reason}", "-", "-", "-")

    console.print(table)
    console.print(f"Wrote {len(files) - failures} results to [bold]{output}[/bold]")
    if failures:
        raise typer.Exit(code=1)


@app.command()
def match(
    resume_path: Annotated[Path, typer.Argument(help="Resume file.", exists=True)],
    job: Annotated[Path, typer.Option("--job", "-j", help="Job description file.", exists=True)],
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
) -> None:
    """Parse a resume and score it against a job description."""
    settings = _bootstrap(verbose)

    async def run() -> tuple[ParseResult, object]:
        service = ResumeParsingService(settings)
        try:
            parsed = await service.parse(resume_path.read_bytes(), filename=resume_path.name)
            requirements = await service.extract_job_requirements(job.read_text(encoding="utf-8"))
            return parsed, service.match(parsed.resume, requirements)
        finally:
            await service.aclose()

    try:
        parsed, result = asyncio.run(run())
    except ResumeParserError as exc:
        _fail(exc.message)
        return

    name = parsed.resume.contact.full_name or resume_path.name
    score = result.score  # type: ignore[attr-defined]
    colour = "green" if score >= 70 else "yellow" if score >= 45 else "red"
    console.print(f"\n[bold]{name}[/bold] -> [{colour}]{score}/100[/{colour}]\n")

    table = Table(title="Score breakdown")
    table.add_column("Dimension")
    table.add_column("Score", justify="right")
    for field, value in result.breakdown.model_dump().items():  # type: ignore[attr-defined]
        table.add_row(field.replace("_", " ").title(), f"{value:.0%}")
    console.print(table)

    if matched := result.matched_skills:  # type: ignore[attr-defined]
        console.print(f"[green]Matched:[/green] {', '.join(matched)}")
    if gaps := result.gaps:  # type: ignore[attr-defined]
        required = [gap.skill for gap in gaps if gap.required]
        preferred = [gap.skill for gap in gaps if not gap.required]
        if required:
            console.print(f"[red]Missing (required):[/red] {', '.join(required)}")
        if preferred:
            console.print(f"[yellow]Missing (preferred):[/yellow] {', '.join(preferred)}")
    for note in result.rationale:  # type: ignore[attr-defined]
        console.print(f"  [dim]- {note}[/dim]")


# ------------------------------------------------------------------------- serving


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address.")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port.")] = None,
    reload: Annotated[bool, typer.Option("--reload", help="Auto-reload on code changes.")] = False,
    workers: Annotated[int, typer.Option(help="Worker processes.")] = 1,
) -> None:
    """Run the HTTP API."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "resume_parser.api.app:create_app",
        factory=True,
        host=host or settings.server.host,
        port=port or settings.server.port,
        reload=reload,
        workers=None if reload else workers,
        log_config=None,  # structlog already owns the root logger.
    )


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="Port for the Streamlit server.")] = 8501,
) -> None:
    """Run the Streamlit interface."""
    try:
        from streamlit.web import cli as streamlit_cli
    except ImportError:
        _fail("Streamlit is not installed. Install the extra: pip install 'resume-ai-parser[ui]'")
        return

    target = Path(__file__).parent / "ui" / "streamlit_app.py"
    sys.argv = ["streamlit", "run", str(target), "--server.port", str(port)]
    streamlit_cli.main()


@app.command()
def schema(
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write to a file.")] = None,
) -> None:
    """Print the strict JSON Schema used to constrain the model."""
    from resume_parser.domain.resume import ResumeExtraction
    from resume_parser.llm.schema import to_strict_json_schema

    payload = json.dumps(to_strict_json_schema(ResumeExtraction), indent=2)
    if output:
        output.write_text(payload, encoding="utf-8")
        console.print(f"[green]Wrote[/green] {output}")
    else:
        console.print_json(payload)


@app.command()
def config() -> None:
    """Show the effective configuration and which providers are usable."""
    settings = get_settings()
    table = Table(title="Effective configuration")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("Environment", settings.environment)
    table.add_row("Model chain", ", ".join(spec.label for spec in settings.llm.models))
    usable = [spec.label for spec in settings.configured_models()]
    table.add_row("Usable models", ", ".join(usable) if usable else "[red]none[/red]")
    table.add_row("Effort", settings.llm.effort)
    table.add_row("Max output tokens", str(settings.llm.max_output_tokens))
    table.add_row("Max file size", f"{settings.extraction.max_file_size / 1_048_576:.1f} MiB")
    table.add_row("Formats", ", ".join(settings.extraction.allowed_formats))
    table.add_row("Cache", "on" if settings.cache.enabled else "off")
    table.add_row("API key required", "yes" if settings.api_key else "no")
    console.print(table)
    if not usable:
        err_console.print(
            "\n[yellow]No provider credentials found.[/yellow] "
            "Set ANTHROPIC_API_KEY, OPENROUTER_API_KEY or OPENAI_API_KEY."
        )


def _print_summary(result: ParseResult) -> None:
    """Render a human-readable digest of a parse."""
    resume = result.resume
    analytics = resume.analytics
    console.print(f"\n[bold]{resume.contact.full_name or 'Unknown candidate'}[/bold]")
    if resume.headline:
        console.print(f"[dim]{resume.headline}[/dim]")

    table = Table(show_header=False, box=None)
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Email", resume.contact.email or "-")
    table.add_row("Phone", resume.contact.phone or "-")
    table.add_row("Location", resume.contact.location or "-")
    table.add_row("Experience", f"{analytics.total_years_of_experience} years")
    table.add_row("Seniority", analytics.seniority_level.value)
    table.add_row("Roles", str(len(resume.experience)))
    table.add_row("Skills", str(len(resume.skills)))
    table.add_row("Top skills", ", ".join(analytics.top_skills[:8]) or "-")
    table.add_row("Completeness", f"{analytics.completeness_score:.0%}")
    table.add_row("Model", result.usage.model)
    cost = result.usage.estimated_cost_usd
    table.add_row("Cost", f"${cost:.4f}" if cost is not None else "-")
    table.add_row("Latency", f"{result.usage.latency_ms} ms")
    console.print(table)

    for warning in result.warnings:
        console.print(f"[yellow]![/yellow] {warning}")


if __name__ == "__main__":  # pragma: no cover
    app()
