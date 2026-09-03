"""Deterministic clean-up of whatever the model returned.

Constrained decoding guarantees the *shape* of the response, not the quality of its
values. A model will happily emit "Jan 2021", "01/2021" and "2021-01" for the same date
across three runs of the same document. Normalizing here means downstream consumers - the
matcher, an ATS import, a dashboard - see one canonical form.

Everything in this module is pure and synchronous, which is what makes it testable without
a network or an API key.
"""

from __future__ import annotations

import re
from datetime import date

from resume_parser.domain.enums import Proficiency, SkillCategory
from resume_parser.domain.resume import ResumeExtraction, Skill

__all__ = [
    "SKILL_ALIASES",
    "canonical_skill_name",
    "normalize_date",
    "normalize_email",
    "normalize_phone",
    "normalize_resume",
    "parse_partial_date",
]

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}  # fmt: skip

_ISO_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$")
_MONTH_NAME_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})$")
_YEAR_MONTH_NAME_RE = re.compile(r"^(\d{4})\s+([A-Za-z]{3,9})$")
_NUMERIC_RE = re.compile(r"^(\d{1,2})[/-](\d{4})$")
_FULL_NUMERIC_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")
_YEAR_ONLY_RE = re.compile(r"^(\d{4})$")
_CURRENT_TOKENS = frozenset(
    {"present", "current", "now", "ongoing", "to date", "till date", "date"}
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_KEEP_RE = re.compile(r"[^\d+]")

#: Canonical names for skills that appear under many spellings. Deliberately short - a
#: hand-curated list of high-frequency collisions beats a fuzzy matcher that silently
#: merges "Java" into "JavaScript".
SKILL_ALIASES: dict[str, str] = {
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ecmascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "reactjs": "React",
    "react.js": "React",
    "react": "React",
    "nodejs": "Node.js",
    "node": "Node.js",
    "node.js": "Node.js",
    "nextjs": "Next.js",
    "next.js": "Next.js",
    "vuejs": "Vue.js",
    "vue": "Vue.js",
    "angularjs": "Angular",
    "angular": "Angular",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "psql": "PostgreSQL",
    "mongo": "MongoDB",
    "mongodb": "MongoDB",
    "mysql": "MySQL",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "docker": "Docker",
    "aws": "AWS",
    "amazon web services": "AWS",
    "gcp": "Google Cloud",
    "google cloud platform": "Google Cloud",
    "google cloud": "Google Cloud",
    "azure": "Azure",
    "microsoft azure": "Azure",
    "ml": "Machine Learning",
    "machine learning": "Machine Learning",
    "dl": "Deep Learning",
    "deep learning": "Deep Learning",
    "nlp": "Natural Language Processing",
    "natural language processing": "Natural Language Processing",
    "ci/cd": "CI/CD",
    "cicd": "CI/CD",
    "rest": "REST APIs",
    "restful apis": "REST APIs",
    "rest api": "REST APIs",
    "rest apis": "REST APIs",
    "golang": "Go",
    "go": "Go",
    "c#": "C#",
    "csharp": "C#",
    "c++": "C++",
    "cpp": "C++",
    "python": "Python",
    "java": "Java",
    "sql": "SQL",
    "nosql": "NoSQL",
    "html5": "HTML",
    "html": "HTML",
    "css3": "CSS",
    "css": "CSS",
    "tf": "TensorFlow",
    "tensorflow": "TensorFlow",
    "pytorch": "PyTorch",
    "torch": "PyTorch",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "scikit-learn": "scikit-learn",
    "pandas": "pandas",
    "numpy": "NumPy",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "spring boot": "Spring Boot",
    "springboot": "Spring Boot",
    "terraform": "Terraform",
    "git": "Git",
    "github actions": "GitHub Actions",
    "llm": "Large Language Models",
    "llms": "Large Language Models",
    "large language models": "Large Language Models",
    "rag": "RAG",
    "genai": "Generative AI",
    "generative ai": "Generative AI",
}

#: Substring rules for bucketing a skill when the model did not classify it.
_CATEGORY_HINTS: tuple[tuple[SkillCategory, frozenset[str]], ...] = (
    (
        SkillCategory.PROGRAMMING_LANGUAGE,
        frozenset({"python", "java", "javascript", "typescript", "go", "rust", "c#", "c++",
                   "ruby", "php", "swift", "kotlin", "scala", "r", "matlab", "perl"}),
    ),
    (
        SkillCategory.DATABASE,
        frozenset({"postgresql", "mysql", "mongodb", "redis", "sql", "nosql", "oracle",
                   "cassandra", "elasticsearch", "dynamodb", "sqlite", "snowflake"}),
    ),
    (
        SkillCategory.CLOUD,
        frozenset({"aws", "azure", "google cloud", "kubernetes", "docker", "terraform",
                   "serverless", "lambda", "ec2", "s3"}),
    ),
    (
        SkillCategory.FRAMEWORK,
        frozenset({"react", "angular", "vue.js", "django", "flask", "fastapi", "spring boot",
                   "next.js", "node.js", "express", "rails", ".net", "pytorch", "tensorflow"}),
    ),
    (
        SkillCategory.METHODOLOGY,
        frozenset({"agile", "scrum", "kanban", "ci/cd", "devops", "tdd", "mlops", "waterfall"}),
    ),
    (
        SkillCategory.SOFT_SKILL,
        frozenset({"leadership", "communication", "teamwork", "mentoring", "collaboration",
                   "problem solving", "stakeholder management"}),
    ),
)  # fmt: skip


def parse_partial_date(value: str | None) -> date | None:
    """Parse the date formats resumes actually contain into a ``date``.

    Missing components default to the first of the month or January, which is the right
    convention for tenure arithmetic: it never overstates a duration.

    Returns ``None`` for unparseable input or for "Present"-style tokens, which are
    represented by ``is_current`` rather than by a date.
    """
    if not value:
        return None
    text = value.strip()
    if not text or text.lower() in _CURRENT_TOKENS:
        return None

    if match := _ISO_RE.match(text):
        year, month, day = match.groups()
        return _safe_date(int(year), int(month or 1), int(day or 1))
    if match := _MONTH_NAME_RE.match(text):
        name, year = match.groups()
        month = _MONTHS.get(name.lower())
        return _safe_date(int(year), month, 1) if month else None
    if match := _YEAR_MONTH_NAME_RE.match(text):
        year, name = match.groups()
        month = _MONTHS.get(name.lower())
        return _safe_date(int(year), month, 1) if month else None
    if match := _NUMERIC_RE.match(text):
        month, year = match.groups()
        return _safe_date(int(year), int(month), 1)
    if match := _FULL_NUMERIC_RE.match(text):
        first, second, year = match.groups()
        century = int(year) if len(year) == 4 else 2000 + int(year)
        # Ambiguous without a locale; treat a >12 first component as a day.
        month, day = (int(second), int(first)) if int(first) > 12 else (int(first), int(second))
        return _safe_date(century, month, day)
    if match := _YEAR_ONLY_RE.match(text):
        return _safe_date(int(match.group(1)), 1, 1)
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    """Build a ``date``, returning ``None`` for impossible values rather than raising."""
    if not 1900 <= year <= 2100:
        return None
    try:
        return date(year, max(1, min(month, 12)), max(1, min(day, 28)))
    except ValueError:  # pragma: no cover - clamping above makes this unreachable
        return None


def normalize_date(value: str | None) -> str | None:
    """Rewrite a date string to ISO ``YYYY-MM``, preserving unparseable input verbatim.

    Keeping the original text when parsing fails is deliberate: silently dropping a date
    we could not read loses information a human reviewer could still use.
    """
    if not value:
        return None
    if value.strip().lower() in _CURRENT_TOKENS:
        return None
    parsed = parse_partial_date(value)
    return parsed.strftime("%Y-%m") if parsed else value.strip()


def normalize_email(value: str | None) -> str | None:
    """Lower-case and validate the shape of an email address."""
    if not value:
        return None
    match = _EMAIL_RE.search(value)
    return match.group(0).lower() if match else None


def normalize_phone(value: str | None) -> str | None:
    """Strip formatting from a phone number, keeping a leading ``+``.

    Deliberately not full E.164: without the candidate's country we cannot add a dial code,
    and guessing one produces confidently wrong data.
    """
    if not value:
        return None
    cleaned = _PHONE_KEEP_RE.sub("", value)
    if cleaned.startswith("+"):
        cleaned = "+" + cleaned[1:].replace("+", "")
    else:
        cleaned = cleaned.replace("+", "")
    digits = sum(char.isdigit() for char in cleaned)
    return cleaned if 7 <= digits <= 15 else value.strip()


def canonical_skill_name(name: str) -> str:
    """Map a skill to its canonical spelling, preserving unknown names as written."""
    key = name.strip().lower().rstrip(".")
    if canonical := SKILL_ALIASES.get(key):
        return canonical
    # Title-case only all-lower input; leave deliberate casing like "PyTorch" alone.
    stripped = name.strip()
    return stripped.title() if stripped.islower() else stripped


def _classify(name: str) -> SkillCategory:
    """Guess a skill's category from its canonical name."""
    lowered = name.lower()
    for category, members in _CATEGORY_HINTS:
        if lowered in members:
            return category
    return SkillCategory.OTHER


def _dedupe_skills(skills: list[Skill]) -> list[Skill]:
    """Collapse duplicate skills, keeping the strongest evidence for each.

    Models routinely emit the same skill twice - once from the skills section and once
    from a role's technology list - with different proficiencies. Keeping the higher of
    the two is the honest merge.
    """
    order = {level: rank for rank, level in enumerate(Proficiency)}
    merged: dict[str, Skill] = {}

    for skill in skills:
        canonical = canonical_skill_name(skill.name)
        if not canonical:
            continue
        category = (
            skill.category if skill.category is not SkillCategory.OTHER else _classify(canonical)
        )
        existing = merged.get(canonical.lower())
        if existing is None:
            merged[canonical.lower()] = Skill(
                name=canonical,
                category=category,
                proficiency=skill.proficiency,
                years_of_experience=skill.years_of_experience,
            )
            continue

        best_proficiency = max(
            (p for p in (existing.proficiency, skill.proficiency) if p is not None),
            key=lambda level: order[level],
            default=None,
        )
        merged[canonical.lower()] = Skill(
            name=canonical,
            category=category if category is not SkillCategory.OTHER else existing.category,
            proficiency=best_proficiency,
            years_of_experience=max(
                (
                    years
                    for years in (existing.years_of_experience, skill.years_of_experience)
                    if years is not None
                ),
                default=None,
            ),
        )
    return list(merged.values())


def normalize_resume(extraction: ResumeExtraction) -> ResumeExtraction:
    """Return a cleaned copy of ``extraction``.

    Dates become ISO, contact details are validated, skills are canonicalized and
    de-duplicated, and empty strings collapse to ``None`` so "absent" has one
    representation instead of three.
    """
    data = extraction.model_copy(deep=True)

    contact = data.contact
    contact.email = normalize_email(contact.email)
    contact.phone = normalize_phone(contact.phone)
    if contact.full_name and not (contact.first_name or contact.last_name):
        parts = contact.full_name.split()
        if len(parts) >= 2:
            contact.first_name, contact.last_name = parts[0], parts[-1]
        elif parts:
            contact.first_name = parts[0]
    if not contact.full_name and (contact.first_name or contact.last_name):
        contact.full_name = " ".join(
            part for part in (contact.first_name, contact.last_name) if part
        )

    for role in data.experience:
        role.start_date = normalize_date(role.start_date)
        normalized_end = normalize_date(role.end_date)
        # A "Present" end date is the signal for an ongoing role, wherever it appears.
        if role.end_date and role.end_date.strip().lower() in _CURRENT_TOKENS:
            role.is_current = True
        role.end_date = None if role.is_current else normalized_end
        role.highlights = [item.strip() for item in role.highlights if item.strip()]
        role.technologies = [
            canonical_skill_name(tech) for tech in role.technologies if tech.strip()
        ]

    for entry in data.education:
        entry.start_date = normalize_date(entry.start_date)
        entry.end_date = normalize_date(entry.end_date)

    for cert in data.certifications:
        cert.issue_date = normalize_date(cert.issue_date)
        cert.expiry_date = normalize_date(cert.expiry_date)

    for project in data.projects:
        project.start_date = normalize_date(project.start_date)
        project.end_date = normalize_date(project.end_date)
        project.technologies = [
            canonical_skill_name(tech) for tech in project.technologies if tech.strip()
        ]

    data.skills = _dedupe_skills(data.skills)
    data.awards = [item.strip() for item in data.awards if item.strip()]
    data.publications = [item.strip() for item in data.publications if item.strip()]
    return data
