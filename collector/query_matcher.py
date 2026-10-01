import re
import unicodedata


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
    required = query_tokens(query)
    if not required:
        return False
    available = set(query_tokens(" ".join(text or "" for text in texts)))
    return all(token in available for token in required)


def job_matches_query(query: str, title: str, details: str | None = None) -> bool:
    required = set(query_tokens(query))
    if not required:
        return False
    title_tokens = set(query_tokens(title))
    if "engineer" in required and "engineer" not in title_tokens:
        return False
    available = title_tokens | set(query_tokens(details or ""))
    return required.issubset(available)


def primary_query_token(query: str) -> str:
    tokens = query_tokens(query)
    technical = [token for token in tokens if token != "engineer"]
    return (technical or list(tokens) or [""])[0]
