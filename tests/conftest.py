"""Shared fixtures.

The whole suite runs without network access or an API key: the LLM is replaced by a stub
provider. That is deliberate - tests that need a real model are slow, flaky and cost money,
so the parts worth testing (extraction, normalization, arithmetic, matching, HTTP contract)
are isolated from the one part that cannot be tested cheaply.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from resume_parser.llm.client import LLMClient
from resume_parser.pipeline.parser import ResumeParsingService
from resume_parser.settings import CacheSettings, LLMSettings, ModelSpec, Settings
from stubs import StubProvider


@pytest.fixture
def sample_resume_payload() -> dict[str, Any]:
    """A realistic extraction payload, as a model would return it."""
    return {
        "contact": {
            "full_name": "Ada Lovelace",
            "email": "ADA@Example.COM",
            "phone": "+1 (415) 555-0142",
            "location": "London, UK",
            "links": {"github": "https://github.com/ada", "other": []},
        },
        "headline": "Senior Machine Learning Engineer",
        "summary": "Engineer with a decade of experience building analytical engines.",
        "experience": [
            {
                "job_title": "Senior Machine Learning Engineer",
                "company": "Analytical Engines Ltd",
                "location": "London",
                "start_date": "Jan 2021",
                "end_date": "Present",
                "is_current": True,
                "highlights": ["Shipped a ranking model", "Mentored three engineers"],
                "technologies": ["python", "pytorch", "k8s"],
            },
            {
                "job_title": "Machine Learning Engineer",
                "company": "Difference Engine Co",
                "start_date": "2018-03",
                "end_date": "2020-12",
                "is_current": False,
                "technologies": ["python", "tf"],
            },
            {
                # Deliberately overlaps the role above - the merge must not double-count.
                "job_title": "Consultant",
                "company": "Self-employed",
                "start_date": "2019-01",
                "end_date": "2020-06",
                "is_current": False,
                "technologies": ["sql"],
            },
        ],
        "education": [
            {
                "degree": "MSc Computer Science",
                "institution": "University of London",
                "start_date": "2016",
                "end_date": "2018",
            }
        ],
        "skills": [
            {"name": "Python", "category": "programming_language", "proficiency": "expert"},
            {"name": "python", "category": "other", "proficiency": "advanced"},
            {"name": "js", "category": "other"},
        ],
        "certifications": [],
        "projects": [],
        "languages": [],
        "awards": [],
        "publications": [],
    }


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    """Settings wired for offline tests."""
    return Settings(
        environment="development",
        anthropic_api_key="test-key",
        llm=LLMSettings(
            models=[
                ModelSpec(
                    provider="anthropic",
                    model="claude-opus-5",
                    input_cost_per_mtok=5.0,
                    output_cost_per_mtok=25.0,
                )
            ],
            max_retries=1,
            retry_base_delay=0.001,
        ),
        cache=CacheSettings(enabled=False),
    )


@pytest.fixture
def stub_provider(sample_resume_payload: dict[str, Any]) -> StubProvider:
    """A provider primed to return the sample payload once."""
    return StubProvider([sample_resume_payload])


@pytest.fixture
def service(settings: Settings, stub_provider: StubProvider) -> ResumeParsingService:
    """A parsing service backed by the stub provider."""
    client = LLMClient(settings, providers={"anthropic": stub_provider})
    return ResumeParsingService(settings, llm=client)


@pytest.fixture
def text_resume_bytes() -> bytes:
    """A plain-text resume large enough to clear the minimum-length check."""
    return (
        b"Ada Lovelace\n"
        b"ada@example.com | +1 415 555 0142 | London, UK\n\n"
        b"SUMMARY\n"
        b"Senior machine learning engineer with ten years of experience designing and "
        b"shipping analytical systems for production use.\n\n"
        b"EXPERIENCE\n"
        b"Senior Machine Learning Engineer, Analytical Engines Ltd (Jan 2021 - Present)\n"
        b"- Shipped a ranking model serving 10M requests per day\n"
        b"- Mentored three engineers\n\n"
        b"Machine Learning Engineer, Difference Engine Co (Mar 2018 - Dec 2020)\n"
        b"- Built the first production recommendation pipeline\n\n"
        b"EDUCATION\n"
        b"MSc Computer Science, University of London (2016 - 2018)\n\n"
        b"SKILLS\n"
        b"Python, PyTorch, Kubernetes, SQL\n"
    )


@pytest.fixture
def minimal_pdf_bytes() -> bytes:
    """A tiny but valid PDF carrying a real text layer."""
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    # pypdf can only write pages, not content streams, so build the object graph directly.
    body = (
        "BT /F1 12 Tf 40 750 Td (Ada Lovelace ada@example.com London UK) Tj "
        "0 -20 Td (Senior Machine Learning Engineer at Analytical Engines Ltd) Tj "
        "0 -20 Td (Jan 2021 to Present. Python PyTorch Kubernetes SQL.) Tj "
        "0 -20 Td (MSc Computer Science University of London 2016 to 2018.) Tj ET"
    )
    objects = [
        "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
        "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj",
        "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        "/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj",
        "4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj",
        f"5 0 obj<</Length {len(body)}>>stream\n{body}\nendstream endobj",
    ]
    out = io.StringIO()
    out.write("%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(out.tell())
        out.write(obj + "\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n")
    out.write(f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF\n")
    _ = PdfWriter  # imported to assert the dependency is present
    return out.getvalue().encode("latin-1")


@pytest.fixture
def docx_resume_bytes() -> bytes:
    """A real DOCX containing both paragraphs and a skills table."""
    docx = pytest.importorskip("docx")

    document = docx.Document()
    document.add_paragraph("Ada Lovelace")
    document.add_paragraph("ada@example.com | +1 415 555 0142 | London, UK")
    document.add_paragraph("Senior Machine Learning Engineer with ten years of experience.")
    document.add_paragraph("Analytical Engines Ltd, Jan 2021 - Present")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Languages"
    table.cell(0, 1).text = "Python, SQL"
    table.cell(1, 0).text = "Frameworks"
    table.cell(1, 1).text = "PyTorch, FastAPI"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
