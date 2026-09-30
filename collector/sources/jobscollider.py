from datetime import datetime, timedelta, timezone

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.utils import strip_html

_API_URL = "https://jobscollider.com/api/search-jobs"
_MAX_PAGES = 5


class JobsColliderSource(JobSource):
    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._jobs_cache: dict[str, list[dict]] = {}

    @property
    def name(self) -> str:
        return "jobscollider"

    def __enter__(self):
        self._client = httpx.Client(
            headers={"User-Agent": "JobAgent/1.0 (job discovery client)"},
            timeout=30,
            follow_redirects=True,
        )
        self._jobs_cache = {}
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._jobs_cache = {}

    def _fetch_jobs(self, title: str) -> list[dict]:
        if title in self._jobs_cache:
            return self._jobs_cache[title]
        jobs: list[dict] = []
        for page in range(_MAX_PAGES):
            try:
                response = self._client.get(_API_URL, params={"query": title, "page": page})
                response.raise_for_status()
                data = response.json()
            except Exception:
                break
            batch = data.get("jobs", []) if isinstance(data, dict) else []
            if not isinstance(batch, list) or not batch:
                break
            jobs.extend(batch)
            if len(batch) < 100:
                break
        self._jobs_cache[title] = jobs
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
        date_matched = 0
        geo_matched = 0

        for job in jobs:
            try:
                published = datetime.fromisoformat(job.get("published_at", "").replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published < cutoff:
                    continue
            except (ValueError, AttributeError):
                continue
            date_matched += 1

            url = job.get("url", "")
            if not url:
                continue
            locations = job.get("locations") or []
            if isinstance(locations, str):
                locations = [locations]
            if locations and not any(location_matches(item, location) for item in locations):
                continue
            geo_matched += 1
            if known_urls and url in known_urls:
                continue

            source_data = {}
            seniority = {
                "entry": "junior",
                "junior": "junior",
                "middle": "mid",
                "mid": "mid",
                "senior": "senior",
                "lead": "lead",
                "director": "director",
            }.get((job.get("seniority") or "").lower())
            if seniority:
                source_data["seniority"] = seniority
            salary_min = job.get("salary_min")
            salary_max = job.get("salary_max")
            source_data["_salary_disclosed"] = any(value not in (None, 0, "") for value in (salary_min, salary_max))
            if salary_min not in (None, 0, ""):
                source_data.update({"salary_min": int(salary_min), "salary_currency": "USD", "salary_period": "yearly"})
            if salary_max not in (None, 0, ""):
                source_data.update({"salary_max": int(salary_max), "salary_currency": "USD", "salary_period": "yearly"})

            location_text = ", ".join(locations) if locations else "Remote"
            if max_results and len(results) >= max_results:
                continue
            results.append(RawJob(
                title=job.get("title") or "",
                company=job.get("company_name") or "",
                location=location_text,
                url=url,
                source=self.name,
                source_id=str(job.get("id") or ""),
                description=strip_html(job.get("description") or ""),
                posted_at=published.isoformat(),
                source_structured_data=source_data or None,
            ))
        self.set_search_diagnostics(
            upstream_found=len(jobs), query_matched=len(jobs),
            date_matched=date_matched, geo_matched=geo_matched,
        )
        return results
