from datetime import datetime, timedelta, timezone
from html import unescape

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.utils import strip_html

_MAX_PAGES = 14


class ArbeitnowSource(JobSource):
    source_name = "arbeitnow"
    api_url = "https://www.arbeitnow.com/api/job-board-api"

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._jobs_cache: list[dict] | None = None

    @property
    def name(self) -> str:
        return self.source_name

    def __enter__(self):
        self._client = httpx.Client(
            headers={"User-Agent": "JobAgent/1.0 (job discovery client)"},
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

    def _fetch_jobs(self) -> list[dict]:
        if self._jobs_cache is not None:
            return self._jobs_cache
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._days_back)
        jobs: list[dict] = []
        for page in range(1, _MAX_PAGES + 1):
            try:
                response = self._client.get(self.api_url, params={"page": page})
                response.raise_for_status()
                data = response.json()
            except Exception:
                break
            batch = data.get("data", []) if isinstance(data, dict) else []
            if not isinstance(batch, list) or not batch:
                break
            jobs.extend(batch)
            oldest = min((item.get("created_at", 0) for item in batch), default=0)
            if oldest and datetime.fromtimestamp(oldest, timezone.utc) < cutoff:
                break
            if not (data.get("links") or {}).get("next"):
                break
        self._jobs_cache = jobs
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
        keyword = title.lower()
        results: list[RawJob] = []

        for job in self._fetch_jobs():
            if max_results and len(results) >= max_results:
                break
            if not job.get("remote") or keyword not in (job.get("title") or "").lower():
                continue
            try:
                published = datetime.fromtimestamp(job.get("created_at"), timezone.utc)
                if published < cutoff:
                    continue
            except (TypeError, ValueError, OSError):
                continue

            url = job.get("url", "")
            if not url or known_urls and url in known_urls:
                continue
            job_location = job.get("location") or ""
            if job_location and not location_matches(job_location, location):
                continue

            results.append(RawJob(
                title=job.get("title") or "",
                company=job.get("company_name") or "",
                location=job_location or "Remote",
                url=url,
                source=self.name,
                source_id=str(job.get("slug") or ""),
                description=strip_html(unescape(job.get("description") or "")),
                posted_at=published.isoformat(),
                source_structured_data={"remote": True},
            ))
        return results


class ArbeitnowUKSource(ArbeitnowSource):
    source_name = "arbeitnow_uk"
    api_url = "https://www.arbeitnow.co.uk/api/job-board-api"
