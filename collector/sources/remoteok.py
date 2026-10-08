"""Remote OK source, public JSON API, no auth required."""
import re
from datetime import datetime, timedelta, timezone

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import job_matches_query, primary_query_token
from collector.taxonomy import classify_text
from collector.utils import strip_html

_API_URL = "https://remoteok.com/api"

_TECHNOLOGY_TAGS = {
    "nodejs": "node", "dotnet": ".net", "go": "golang",
}
_ROLE_TAGS = {
    "backend": "backend", "frontend": "frontend", "fullstack": "full-stack",
    "qa": "qa", "mobile": "mobile", "devops": "devops",
    "data": "data", "ml": "machine-learning", "security": "security",
    "software_engineering": "software",
}


def _tag_for_query(query: str) -> str:
    classification = classify_text(query)
    if classification.technologies:
        technology = sorted(classification.technologies)[0]
        return _TECHNOLOGY_TAGS.get(technology, technology)
    for role in _ROLE_TAGS:
        if role in classification.role_families:
            return _ROLE_TAGS[role]
    return primary_query_token(query)


def _publication_date(job: dict) -> datetime | None:
    value = job.get("date")
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        except (ValueError, TypeError):
            pass
    try:
        return datetime.fromtimestamp(int(job.get("epoch")), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _location_is_eligible(location: str, description: str, candidate_location: str) -> bool:
    if location.strip():
        return location_matches(location, candidate_location)
    restriction = re.search(
        r"\b(?:remote(?: work)?|work(?:ing)? remotely|candidates?|applicants?|employees?|hire|hiring)\b"
        r"[^.!?\n]{0,80}\b(?:within|in|from|based in|located in|reside in|residents? of)\b"
        r"[^.!?\n]{0,80}",
        description,
        re.IGNORECASE,
    )
    return not restriction or location_matches(restriction.group(0), candidate_location)


def _source_facts(job: dict, location: str) -> dict:
    facts = {
        "remote": True,
        "remote_available": True,
        "remote_regions": [location] if location else [],
    }
    salary_values = []
    for key in ("salary_min", "salary_max"):
        try:
            value = int(job.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            facts[key] = value
            salary_values.append(value)
    facts["_salary_disclosed"] = bool(salary_values)
    if salary_values:
        facts.update(salary_currency="USD", salary_period="yearly")
    return facts


class RemoteOKSource(JobSource):
    requires_stealth_pauses = False

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._generic_cache: list[dict] | None = None
        self._tag_cache: dict[str, list[dict]] = {}
        self._tag_errors: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "remoteok"

    def __enter__(self):
        self._client = httpx.Client(
            headers={"User-Agent": "JobAgent/1.0 (job aggregator)"},
            timeout=30,
            follow_redirects=True,
        )
        self._generic_cache = None
        self._tag_cache = {}
        self._tag_errors = {}
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._generic_cache = None
        self._tag_cache = {}
        self._tag_errors = {}

    def login(self) -> None:
        pass

    def _fetch_url(self, url: str) -> list[dict]:
        response = self._client.get(url)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or len(data) < 2:
            return []
        return [item for item in data[1:] if isinstance(item, dict)]

    def _fetch_jobs(self, tag: str) -> list[dict]:
        if self._generic_cache is None:
            self._generic_cache = self._fetch_url(_API_URL)

        by_url: dict[str, dict] = {j["url"]: j for j in self._generic_cache if j.get("url")}

        if tag:
            if tag not in self._tag_cache:
                try:
                    self._tag_cache[tag] = self._fetch_url(f"{_API_URL}?tags={tag}")
                except (httpx.HTTPError, ValueError) as error:
                    self._tag_cache[tag] = []
                    self._tag_errors[tag] = str(error)[:1000]
            for j in self._tag_cache[tag]:
                u = j.get("url")
                if u:
                    by_url.setdefault(u, j)

        return list(by_url.values())

    def search(
        self,
        title: str,
        location: str,
        days_back: int | None = None,
        max_results: int | None = None,
        known_urls: set[str] | None = None,
    ) -> list[RawJob]:
        days    = days_back if days_back is not None else self._days_back
        cutoff  = datetime.now(timezone.utc) - timedelta(days=days)
        tag = _tag_for_query(title)

        jobs = self._fetch_jobs(tag)

        results: list[RawJob] = []
        query_matched = 0
        date_matched = 0
        geo_matched = 0
        known_url_filtered = 0
        for job in jobs:
            position = job.get("position", "")
            description = strip_html(job.get("description", ""))
            if not job_matches_query(title, position, description):
                continue
            query_matched += 1

            pub_dt = _publication_date(job)
            if not pub_dt or pub_dt < cutoff:
                continue
            date_matched += 1

            url = job.get("url", "")
            if not url:
                continue

            job_location = job.get("location", "") or ""
            if not _location_is_eligible(job_location, description or "", location):
                continue
            geo_matched += 1
            if known_urls and url in known_urls:
                known_url_filtered += 1
                continue
            if max_results and len(results) >= max_results:
                continue

            results.append(RawJob(
                title=position,
                company=job.get("company", ""),
                location=job_location or "Remote",
                url=url,
                source="remoteok",
                source_id=str(job.get("id") or job.get("slug") or ""),
                description=description,
                posted_at=pub_dt.isoformat(),
                source_structured_data=_source_facts(job, job_location),
            ))

        diagnostics = {
            "upstream_found": len(jobs), "query_matched": query_matched,
            "date_matched": date_matched, "geo_matched": geo_matched,
            "known_url_filtered": known_url_filtered,
        }
        if tag in self._tag_errors:
            diagnostics.update(source_status="partial", source_error=self._tag_errors[tag])
        self.set_search_diagnostics(**diagnostics)
        return results
