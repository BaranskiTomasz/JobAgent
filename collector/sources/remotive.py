"""Remotive.io source, free public JSON API, no auth required."""
from datetime import datetime, timedelta, timezone

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import job_matches_query
from collector.utils import strip_html

_API_URL = "https://remotive.com/api/remote-jobs"


class RemotiveSource(JobSource):
    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._jobs_cache: list[dict] | None = None

    @property
    def name(self) -> str:
        return "remotive"

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
        payload = response.json()
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        self._jobs_cache = jobs if isinstance(jobs, list) else []
        return self._jobs_cache

    def search(
        self,
        title: str,
        location: str,
        days_back: int | None = None,
        max_results: int | None = None,
        known_urls: set[str] | None = None,
    ) -> list[RawJob]:
        days   = days_back if days_back is not None else self._days_back
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        jobs = self._fetch_jobs()

        results: list[RawJob] = []
        query_matched = 0
        date_matched = 0
        geo_matched = 0
        for job in jobs:
            if max_results and len(results) >= max_results:
                break
            description = strip_html(job.get("description", ""))
            if not job_matches_query(title, job.get("title", ""), description):
                continue
            query_matched += 1

            # Date filter
            pub_str = job.get("publication_date", "")
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except (ValueError, AttributeError):
                continue
            date_matched += 1

            url = job.get("url", "")
            if not url:
                continue

            candidate_loc = job.get("candidate_required_location", "")
            if not location_matches(candidate_loc, location):
                continue
            geo_matched += 1
            if known_urls and url in known_urls:
                continue

            results.append(RawJob(
                title=job.get("title", ""),
                company=job.get("company_name", ""),
                location=candidate_loc or "Remote",
                url=url,
                source="remotive",
                source_id=str(job.get("id", "")),
                description=description,
                posted_at=pub_dt.isoformat(),
                source_structured_data={
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": [candidate_loc] if candidate_loc else [],
                },
            ))

        self.set_search_diagnostics(
            upstream_found=len(jobs), query_matched=query_matched,
            date_matched=date_matched, geo_matched=geo_matched,
        )
        return results
