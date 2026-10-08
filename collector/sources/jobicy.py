"""Jobicy's public remote-jobs API (no authentication required)."""
from datetime import datetime, timedelta, timezone

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import job_matches_query
from collector.utils import strip_html

_API_URL = "https://jobicy.com/api/v2/remote-jobs"
_PAGE_SIZE = 200


def _published_at(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _number(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class JobicySource(JobSource):
    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._jobs_cache: dict[str, list[dict]] = {}
        self._diagnostics_cache: dict[str, dict[str, object]] = {}
        self._fetch_diagnostics: dict[str, object] = {}

    @property
    def name(self) -> str:
        return "jobicy"

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

    def login(self) -> None:
        pass

    def _fetch_jobs(self, title: str) -> list[dict]:
        cache_key = title.strip().casefold()
        if cache_key in self._jobs_cache:
            self._fetch_diagnostics = dict(self._diagnostics_cache[cache_key])
            return self._jobs_cache[cache_key]
        if self._client is None:
            raise RuntimeError("Jobicy source must be used as a context manager")
        params: dict[str, object] = {"count": _PAGE_SIZE}
        tag = title.strip()
        if 3 <= len(tag) <= 50:
            params["tag"] = tag

        jobs: list[dict] = []
        cursor: str | None = None
        cursors: set[str] = set()
        pages = 0
        fetch_diagnostics: dict[str, object] = {}
        while True:
            request_params = dict(params)
            if cursor:
                request_params["cursor"] = cursor
            response = self._client.get(_API_URL, params=request_params)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get("success") is False:
                raise ValueError(str(data.get("error") or "Jobicy API returned success=false"))
            if not isinstance(data, dict) or not isinstance(data.get("jobs", []), list):
                raise ValueError("Jobicy API returned an invalid jobs payload")
            jobs.extend(job for job in data["jobs"] if isinstance(job, dict))
            pages += 1
            next_cursor = data.get("nextCursor")
            if not next_cursor:
                if data.get("hasMore") is True:
                    fetch_diagnostics.update(
                        partial=True,
                        source_status="partial",
                        source_error="hasMore is true but nextCursor is missing",
                    )
                break
            if not isinstance(next_cursor, str) or next_cursor in cursors:
                fetch_diagnostics.update(
                    partial=True,
                    source_status="partial",
                    source_error="repeated or invalid nextCursor",
                )
                break
            cursors.add(next_cursor)
            cursor = next_cursor
        fetch_diagnostics.update(
            api_pages=pages,
            api_complete=not fetch_diagnostics.get("partial", False),
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
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        jobs = self._fetch_jobs(title)
        results: list[RawJob] = []
        query_matched = date_matched = geo_matched = known_url_filtered = 0
        for job in jobs:
            description = strip_html(job.get("jobDescription") or "")
            if not job_matches_query(title, job.get("jobTitle") or "", description or ""):
                continue
            query_matched += 1
            published = _published_at(job.get("pubDate"))
            if published is None or published < cutoff:
                continue
            date_matched += 1
            url = job.get("url") or ""
            if not isinstance(url, str) or not url:
                continue
            job_location = job.get("jobGeo") or ""
            if not location_matches(str(job_location), location):
                continue
            geo_matched += 1
            if known_urls and url in known_urls:
                known_url_filtered += 1
                continue
            if max_results and len(results) >= max_results:
                continue

            source_data = {
                "remote": True,
                "remote_available": True,
                "remote_regions": [job_location] if job_location else [],
            }
            level = (job.get("jobLevel") or "").lower()
            seniority = {
                "entry level": "junior",
                "junior": "junior",
                "mid level": "mid",
                "mid": "mid",
                "senior": "senior",
                "lead": "lead",
                "director": "director",
            }.get(level)
            if seniority:
                source_data["seniority"] = seniority
            salary_min, salary_max = _number(job.get("salaryMin")), _number(job.get("salaryMax"))
            source_data["_salary_disclosed"] = salary_min is not None or salary_max is not None
            if salary_min is not None:
                source_data["salary_min"] = salary_min
            if salary_max is not None:
                source_data["salary_max"] = salary_max
            currency = str(job.get("salaryCurrency") or "").upper()
            if currency in {"PLN", "EUR", "USD", "GBP"}:
                source_data["salary_currency"] = currency
            period = str(job.get("salaryPeriod") or "").lower()
            if period in {"hourly", "monthly", "yearly"}:
                source_data["salary_period"] = period
            source_id = str(
                job.get("id") or job.get("jobSlug") or url.rstrip("/").rsplit("/", 1)[-1]
            )
            results.append(RawJob(
                title=job.get("jobTitle") or "",
                company=job.get("companyName") or "",
                location=str(job_location) or "Remote",
                url=url,
                source=self.name,
                source_id=source_id,
                description=description,
                posted_at=published.isoformat(),
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
