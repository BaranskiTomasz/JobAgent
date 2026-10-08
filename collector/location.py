"""Shared location-matching logic for API-based job sources."""

import re


def workplace_suffix(modes: set[str]) -> str:
    # An offer can legitimately advertise more than one mode; remote wins the
    # label since it's the strictest claim a downstream geo check needs.
    if "remote" in modes:
        return " (Remote)"
    if "hybrid" in modes:
        return " (Hybrid)"
    return ""

_EU_COUNTRIES = frozenset({
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czech republic",
    "denmark", "estonia", "finland", "france", "germany", "greece", "hungary",
    "ireland", "italy", "latvia", "liechtenstein", "lithuania", "luxembourg",
    "malta", "netherlands", "norway", "poland", "portugal", "romania",
    "slovakia", "slovenia", "spain", "sweden", "switzerland", "united kingdom",
})

_WORLDWIDE_TOKENS = frozenset({"worldwide", "anywhere", "global", "international"})
_EUROPE_TOKENS    = frozenset({"europe", "european", "emea", "eea"})
_NA_TOKENS        = frozenset({"north america", "usa/canada", "canada/usa", "americas"})

_COUNTRY_ALIASES: dict[str, str] = {
    "us":            "united states",
    "usa":           "united states",
    "u.s.":          "united states",
    "uk":            "united kingdom",
    "gb":            "united kingdom",
    "great britain": "united kingdom",
    "deutschland":   "germany",
    "polska":        "poland",
    "pl":            "poland",
}

_REMOTE_TERMS = frozenset({"remote", "zdalne", "zdalnie", "zdalny"})

# Timezone abbreviations that indicate a Central/Western/Eastern Europe work schedule.
# "Time zone: CET (+/- 3 hours)" and "CET (+/- 3 hours)" are used by WorkingNomads.
_EU_TIMEZONE_TOKENS = frozenset({"cet", "cest", "eet", "eest", "wet", "west"})


def location_matches(job_location: str, search_location: str) -> bool:
    job_required_location = job_location.lower()
    raw_search = search_location.lower().strip()
    normalized_search = _COUNTRY_ALIASES.get(raw_search, raw_search)

    def contains(value: str) -> bool:
        return bool(re.search(rf"(?<![\w]){re.escape(value)}(?![\w])", job_required_location))

    country_names = {normalized_search}
    country_names.update(alias for alias, country in _COUNTRY_ALIASES.items() if country == normalized_search)
    exclusions = ("except", "excluding", "excluded", "not available in", "outside")
    if any(
        re.search(rf"\b{re.escape(marker)}\s+(?:of\s+)?{re.escape(country)}\b", job_required_location)
        for marker in exclusions
        for country in country_names
    ):
        return False

    if normalized_search in _REMOTE_TERMS:
        return True
    scoped_anywhere = bool(re.search(r"\banywhere\s+in\b", job_required_location))
    if not job_required_location or any(contains(token) for token in _WORLDWIDE_TOKENS if token != "anywhere"):
        return True
    if contains("anywhere") and not scoped_anywhere:
        return True
    if normalized_search in _EU_COUNTRIES and (
        any(t in job_required_location for t in _EUROPE_TOKENS)
        or bool(re.search(r"(?<![a-z])eu(?![a-z])", job_required_location))
    ):
        return True
    if normalized_search in _EU_COUNTRIES and any(t in job_required_location for t in _EU_TIMEZONE_TOKENS):
        return True
    if normalized_search in ("united states", "canada") and any(t in job_required_location for t in _NA_TOKENS):
        return True
    if contains(normalized_search):
        return True
    aliases = [k for k, v in _COUNTRY_ALIASES.items() if v == normalized_search]
    return any(contains(alias) for alias in aliases)
