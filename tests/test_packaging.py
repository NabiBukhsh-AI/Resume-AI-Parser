"""Guards on the packaging metadata.

An invalid trove classifier does not fail an editable install or the test suite - it fails
at *build* time, which means it surfaces in CI or, worse, at release. These checks are cheap
and move that feedback to the local run.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from resume_parser import __version__

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@pytest.fixture(scope="module")
def project() -> dict[str, Any]:
    """The `[project]` table from pyproject.toml."""
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]  # type: ignore[no-any-return]


def test_classifiers_are_recognised_by_pypi(project: dict[str, Any]) -> None:
    """Every classifier must exist in the trove list, or `uv build` fails."""
    trove = pytest.importorskip("trove_classifiers")
    unknown = [c for c in project["classifiers"] if c not in trove.classifiers]
    assert not unknown, f"Unknown trove classifiers: {unknown}"


def test_version_matches_the_package(project: dict[str, Any]) -> None:
    """`__version__` and the distribution version must not drift apart."""
    assert project["version"] == __version__


def test_required_metadata_is_present(project: dict[str, Any]) -> None:
    for key in ("name", "description", "readme", "license", "requires-python", "authors"):
        assert project.get(key), f"pyproject is missing `{key}`"


def test_description_is_a_single_useful_line(project: dict[str, Any]) -> None:
    description = project["description"]
    assert "\n" not in description
    assert 40 <= len(description) <= 300


def test_urls_point_at_the_repository(project: dict[str, Any]) -> None:
    urls = project["urls"]
    assert "Homepage" in urls
    assert "Issues" in urls
    for name, url in urls.items():
        assert url.startswith("https://"), f"{name} is not an https URL: {url}"


def test_cli_entry_point_resolves(project: dict[str, Any]) -> None:
    """The console script must name a real, importable callable."""
    import importlib

    target = project["scripts"]["resume-parser"]
    module_name, _, attribute = target.partition(":")
    module = importlib.import_module(module_name)
    assert callable(getattr(module, attribute))


def test_changelog_documents_the_current_version() -> None:
    changelog = PYPROJECT.parent / "CHANGELOG.md"
    assert f"[{__version__}]" in changelog.read_text(encoding="utf-8")
