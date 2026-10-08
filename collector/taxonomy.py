import re
import unicodedata
from dataclasses import dataclass


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9+#.]+", normalized))


TECHNOLOGY_ALIASES: dict[str, tuple[str, ...]] = {
    "php": ("php",),
    "python": ("python", "django", "fastapi", "flask"),
    "nodejs": ("node", "node.js", "nodejs", "nestjs", "express.js"),
    "react": ("react", "react.js", "reactjs", "next.js", "nextjs"),
    "angular": ("angular", "angular.js", "angularjs"),
    "java": ("java", "spring", "spring boot"),
    "dotnet": (".net", "dotnet", "asp.net", "c#"),
    "go": ("golang", "go developer", "go engineer"),
    "ruby": ("ruby", "ruby on rails", "rails"),
    "kotlin": ("kotlin",),
    "typescript": ("typescript",),
    "javascript": ("javascript",),
    "rust": ("rust",),
    "cpp": ("c++", "cpp"),
    "swift": ("swift",),
}

ROLE_FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "software_engineering": (
        "software engineer", "software developer", "application developer",
        "application engineer", "web developer", "product engineer",
    ),
    "backend": ("backend", "back end", "server side"),
    "frontend": ("frontend", "front end", "ui engineer", "web ui"),
    "fullstack": ("fullstack", "full stack"),
    "qa": (
        "qa", "quality assurance", "quality engineer", "test engineer",
        "test automation", "automation tester", "software tester", "sdet",
    ),
    "mobile": ("mobile", "android", "ios"),
    "devops": ("devops", "platform engineer", "site reliability", "sre"),
    "data": ("data engineer", "analytics engineer", "data platform"),
    "ml": ("machine learning", "ml engineer", "ai engineer", "artificial intelligence"),
    "security": ("security engineer", "application security", "cybersecurity"),
    "management": ("engineering manager", "head of engineering", "vp engineering", "cto"),
}


@dataclass(frozen=True)
class Classification:
    technologies: frozenset[str]
    role_families: frozenset[str]


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = normalize_text(phrase)
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized_phrase)}(?![a-z0-9])", text))


def classify_text(*values: str | None) -> Classification:
    text = normalize_text(" ".join(value or "" for value in values))
    technologies = {
        category
        for category, aliases in TECHNOLOGY_ALIASES.items()
        if any(_contains_phrase(text, alias) for alias in aliases)
    }
    role_families = {
        category
        for category, aliases in ROLE_FAMILY_ALIASES.items()
        if any(_contains_phrase(text, alias) for alias in aliases)
    }
    software_specializations = {
        "backend", "frontend", "fullstack", "mobile", "devops", "data", "ml", "security",
    }
    if role_families & software_specializations:
        role_families.add("software_engineering")
    return Classification(frozenset(technologies), frozenset(role_families))
