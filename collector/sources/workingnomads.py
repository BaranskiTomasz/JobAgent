"""Working Nomads source, public JSON API, no auth required."""
from datetime import datetime, timedelta, timezone

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import job_matches_query
from collector.utils import strip_html

_API_URL  = "https://www.workingnomads.com/api/exposed_jobs/"
_BASE_URL = "https://www.workingnomads.com"


def _canonical_url(url: str) -> str:
    if url.startswith("http"):
        return url
    return _BASE_URL + (url if url.startswith("/") else f"/{url}")



class WorkingNomadsSource(JobSource):
    requires_stealth_pauses = False

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._jobs_cache: list[dict] | None = None

    @property
    def name(self) -> str:
        return "workingnomads"

    def __enter__(self):
        self._client = httpx.Client(
            headers={"User-Agent": "JobAgent/1.0 (job aggregator)"},
            timeout=30,
            follow_redirects=True,
        )
        self._jobs_cache = None
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._jobs_cache = None

    def login(self) -> None:
        pass

    def _fetch_jobs(self) -> list[dict]:
        if self._jobs_cache is not None:
            return self._jobs_cache
        response = self._client.get(_API_URL)
        response.raise_for_status()
        data = response.json()
        self._jobs_cache = data if isinstance(data, list) else []
        return self._jobs_cache

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
        jobs = self._fetch_jobs()

        results: list[RawJob] = []
        query_matched = 0
        date_matched = 0
        geo_matched = 0
        known_url_filtered = 0
        for job in jobs:
            job_title = job.get("title", "")
            description = strip_html(job.get("description", ""))
            tags = job.get("tags", "") or ""
            if not job_matches_query(title, job_title, f"{tags}\n{description or ''}"):
                continue
            query_matched += 1

            pub_str = job.get("pub_date", "")
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except (ValueError, AttributeError):
                continue
            date_matched += 1

            raw_url = job.get("url", "")
            if not raw_url:
                continue
            url = _canonical_url(raw_url)

            job_location = job.get("location", "") or ""
            if not location_matches(job_location, location):
                continue
            geo_matched += 1
            if known_urls and url in known_urls:
                known_url_filtered += 1
                continue
            if max_results and len(results) >= max_results:
                continue

            source_id = url.rstrip("/").rsplit("/", 1)[-1]

            results.append(RawJob(
                title=job_title,
                company=job.get("company_name", ""),
                location=job.get("location", "") or "Remote",
                url=url,
                source="workingnomads",
                source_id=source_id,
                description=description,
                posted_at=pub_dt.isoformat(),
                source_structured_data={
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": [job_location] if job_location else [],
                },
            ))

        self.set_search_diagnostics(
            upstream_found=len(jobs), query_matched=query_matched,
            date_matched=date_matched, geo_matched=geo_matched,
            known_url_filtered=known_url_filtered,
        )
        return results
