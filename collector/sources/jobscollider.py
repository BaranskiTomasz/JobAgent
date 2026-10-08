"""JobsCollider's public search endpoint, without authentication."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import job_matches_query
from collector.utils import strip_html

_API_URL = "https://jobscollider.com/api/search-jobs"
_PAGE_SIZE = 100
_MAX_PAGES = 5


def _published_at(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _number(value: object) -> int | float | None:
    if value in (None, "", 0):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _url_key(url: str) -> str:
    parsed = urlsplit(url.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def _location_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    values = []
    for item in value:
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
        elif isinstance(item, dict):
            for key in ("name", "location", "country", "city"):
                text = item.get(key)
                if text:
                    values.append(str(text).strip())
                    break
    return list(dict.fromkeys(values))


class JobsColliderSource(JobSource):
    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._jobs_cache: dict[str, list[dict]] = {}
        self._diagnostics_cache: dict[str, dict[str, object]] = {}
        self._fetch_diagnostics: dict[str, object] = {}

    @property
    def name(self) -> str:
        return "jobscollider"

    def __enter__(self):
        self._client = httpx.Client(
            headers={
                "User-Agent": "JobAgent/1.0 (job discovery client)",
                "Accept": "application/json",
            },
            timeout=30,
            follow_redirects=True,
        )
        self._jobs_cache = {}
        self._diagnostics_cache = {}
        self._fetch_diagnostics = {}
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._jobs_cache = {}
        self._diagnostics_cache = {}
        self._fetch_diagnostics = {}

    def _fetch_jobs(self, title: str) -> list[dict]:
        cache_key = title.strip().casefold()
        if cache_key in self._jobs_cache:
            self._fetch_diagnostics = dict(self._diagnostics_cache[cache_key])
            return self._jobs_cache[cache_key]
        if self._client is None:
            raise RuntimeError("JobsCollider source must be used as a context manager")
        jobs: list[dict] = []
        seen_keys: set[str] = set()
        pages = 0
        fetch_diagnostics: dict[str, object] = {}
        try:
            for page in range(_MAX_PAGES):
                response = self._client.get(
                    _API_URL,
                    params={"query": title.strip(), "page": page, "limit": _PAGE_SIZE},
                )
                response.raise_for_status()
                data = response.json()
                batch = data.get("jobs", []) if isinstance(data, dict) else data
                if not isinstance(batch, list) or not batch:
                    break
                pages += 1
                for job in batch:
                    if not isinstance(job, dict):
                        continue
                    raw_url = str(job.get("url") or "").strip()
                    key = _url_key(raw_url) if raw_url else str(job.get("id") or len(jobs))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        jobs.append(job)
                if len(batch) < _PAGE_SIZE:
                    break
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            fetch_diagnostics.update(
                source_status="partial" if jobs else "error",
                source_error=f"API request failed: {exc}",
            )
        fetch_diagnostics.update(
            api_pages=pages,
            api_complete=pages < _MAX_PAGES and "source_error" not in fetch_diagnostics,
            api_page_limit_reached=pages == _MAX_PAGES,
        )
        self._fetch_diagnostics = fetch_diagnostics
        self._diagnostics_cache[cache_key] = dict(fetch_diagnostics)
        self._jobs_cache[cache_key] = jobs
        return jobs

    def search(
        self,
        title: str,
        location: str,
        days_back: int | None = None,
        max_results: int | None = None,
        known_urls: set[str] | None = None,
    ) -> list[RawJob]:
        days = days_back if days_back is not None else self._days_back
        cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
        jobs = self._fetch_jobs(title)
        known_keys = {_url_key(url) for url in (known_urls or set())}
        results: list[RawJob] = []
        seen_keys: set[str] = set()
        query_matched = date_matched = geo_matched = known_url_filtered = 0
        for job in jobs:
            job_title = str(job.get("title") or job.get("job_title") or "").strip()
            description = strip_html(str(job.get("description") or "")) or ""
            details = "\n".join(
                str(job.get(key) or "") for key in ("category", "tags", "skills")
            )
            if title.strip() and not job_matches_query(title, job_title, details + "\n" + description):
                continue
            query_matched += 1
            published = _published_at(job.get("published_at") or job.get("date_posted") or job.get("created_at"))
            if cutoff and (published is None or published < cutoff):
                continue
            date_matched += 1
            url = str(job.get("url") or job.get("apply_url") or "").strip()
            if not url:
                continue
            url_key = _url_key(url)
            if url_key in known_keys:
                known_url_filtered += 1
                continue
            if url_key in seen_keys:
                continue
            locations = _location_values(job.get("locations") or job.get("location"))
            location_text = ", ".join(locations) if locations else "Remote"
            if locations and not any(location_matches(item, location) for item in locations):
                continue
            geo_matched += 1
            if max_results and len(results) >= max_results:
                continue
            source_data: dict[str, object] = {
                "remote_regions": locations,
                "_salary_disclosed": False,
            }
            seniority = {
                "entry": "junior",
                "entry level": "junior",
                "junior": "junior",
                "middle": "mid",
                "mid": "mid",
                "senior": "senior",
                "lead": "lead",
                "director": "director",
            }.get(str(job.get("seniority") or job.get("job_level") or "").casefold())
            if seniority:
                source_data["seniority"] = seniority
            salary_min = _number(job.get("salary_min"))
            salary_max = _number(job.get("salary_max"))
            if salary_min is not None:
                source_data["salary_min"] = salary_min
            if salary_max is not None:
                source_data["salary_max"] = salary_max
            source_data["_salary_disclosed"] = salary_min is not None or salary_max is not None
            currency = str(job.get("salary_currency") or job.get("currency") or "").upper()
            if (salary_min is not None or salary_max is not None) and not currency:
                currency = "USD"
            if currency:
                source_data["salary_currency"] = currency
            period = str(job.get("salary_period") or "").lower()
            if (salary_min is not None or salary_max is not None) and not period:
                period = "yearly"
            if period in {"hourly", "monthly", "yearly"}:
                source_data["salary_period"] = period
            seen_keys.add(url_key)
            results.append(RawJob(
                title=job_title,
                company=str(job.get("company_name") or job.get("company") or ""),
                location=location_text,
                url=url,
                source=self.name,
                source_id=str(job.get("id") or url_key),
                description=description,
                posted_at=published.isoformat() if published else None,
                source_structured_data=source_data,
            ))
        diagnostics = dict(self._fetch_diagnostics)
        diagnostics.update(
            upstream_found=len(jobs),
            query_matched=query_matched,
            date_matched=date_matched,
            geo_matched=geo_matched,
            known_url_filtered=known_url_filtered,
        )
        diagnostics.setdefault("source_status", "empty" if not jobs else "ok")
        self.set_search_diagnostics(**diagnostics)
        return results
