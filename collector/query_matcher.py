import re
import unicodedata

from collector.taxonomy import classify_text


_IGNORED = {
    "a", "an", "and", "of", "the", "for", "with", "remote",
    "junior", "jr", "mid", "middle", "senior", "sr", "lead", "principal", "staff",
}

_ALIASES = {
    "developer": "engineer",
    "dev": "engineer",
    "programmer": "engineer",
    "development": "engineer",
    "engineering": "engineer",
    "backend": "backend",
    "back-end": "backend",
    "frontend": "frontend",
    "front-end": "frontend",
    "fullstack": "fullstack",
    "full-stack": "fullstack",
    "dotnet": "net",
    ".net": "net",
    "nodejs": "node",
    "node.js": "node",
}


def query_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKD", value.lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    raw = re.findall(r"[a-z0-9+#.-]+", normalized)
    tokens = []
    for token in raw:
        mapped = _ALIASES.get(token, token).strip(".-")
        if mapped and mapped not in _IGNORED and mapped not in tokens:
            tokens.append(mapped)
    return tuple(tokens)


def query_matches(query: str, *texts: str | None) -> bool:
    requested = classify_text(query)
    available_categories = classify_text(*texts)
    if requested.technologies and not requested.technologies.issubset(available_categories.technologies):
        return False
    if requested.role_families and not requested.role_families.issubset(available_categories.role_families):
        return False
    if requested.technologies or requested.role_families:
        return True
    required = query_tokens(query)
    if not required:
        return False
    available = set(query_tokens(" ".join(text or "" for text in texts)))
    return all(token in available for token in required)


def job_matches_query(query: str, title: str, details: str | None = None) -> bool:
    requested = classify_text(query)
    title_categories = classify_text(title)
    all_categories = classify_text(title, details)
    if requested.role_families and not requested.role_families.issubset(title_categories.role_families):
        return False
    if requested.technologies and not requested.technologies.issubset(all_categories.technologies):
        return False
    if requested.technologies or requested.role_families:
        disallowed_families = {"security", "management"}
        if requested.technologies and title_categories.role_families & disallowed_families:
            return False
        customer_title = title.casefold()
        if requested.technologies and any(value in customer_title for value in ("customer engineer", "support engineer", "solutions engineer")):
            return False
        if requested.technologies and not title_categories.technologies:
            allowed_generic = {"software_engineering", "backend", "frontend", "fullstack", "qa", "mobile", "devops", "data", "ml"}
            if not title_categories.role_families & allowed_generic:
                return False
        return True
    required = set(query_tokens(query))
    if not required:
        return False
    title_tokens = set(query_tokens(title))
    if "engineer" in required and "engineer" not in title_tokens:
        return False
    technical = required - {"engineer"}
    normalized_title = title.casefold().replace("-", " ")
    generic_software_role = any(marker in normalized_title for marker in (
        "software", "backend", "frontend", "front end",
        "fullstack", "full stack", "web application",
    ))
    if technical and not technical.issubset(title_tokens) and not generic_software_role:
        return False
    available = title_tokens | set(query_tokens(details or ""))
    return required.issubset(available)


def primary_query_token(query: str) -> str:
    tokens = query_tokens(query)
    technical = [token for token in tokens if token != "engineer"]
    return (technical or list(tokens) or [""])[0]
