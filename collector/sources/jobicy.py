from datetime import datetime, timedelta, timezone

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.utils import strip_html

_API_URL = "https://jobicy.com/api/v2/remote-jobs"


class JobicySource(JobSource):
    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._jobs_cache: dict[str, list[dict]] = {}

    @property
    def name(self) -> str:
        return "jobicy"

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
        try:
            params = {"count": 200}
            if title.casefold() != "qa":
                params["tag"] = title
            response = self._client.get(_API_URL, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception:
            self._jobs_cache[title] = []
            return []
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        self._jobs_cache[title] = jobs if isinstance(jobs, list) else []
        return self._jobs_cache[title]

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
        results: list[RawJob] = []

        for job in self._fetch_jobs(title):
            if max_results and len(results) >= max_results:
                break
            try:
                published = datetime.fromisoformat(job.get("pubDate", "").replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published < cutoff:
                    continue
            except (ValueError, AttributeError):
                continue

            url = job.get("url", "")
            if not url or known_urls and url in known_urls:
                continue
            job_location = job.get("jobGeo") or ""
            if not location_matches(job_location, location):
                continue

            source_data = {"remote": True, "remote_regions": [job_location] if job_location else []}
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

            salary_min = job.get("salaryMin")
            salary_max = job.get("salaryMax")
            source_data["_salary_disclosed"] = any(value not in (None, 0, "") for value in (salary_min, salary_max))
            if salary_min not in (None, 0, ""):
                source_data["salary_min"] = int(salary_min)
            if salary_max not in (None, 0, ""):
                source_data["salary_max"] = int(salary_max)
            currency = job.get("salaryCurrency")
            if currency in {"PLN", "EUR", "USD", "GBP"}:
                source_data["salary_currency"] = currency
            period = (job.get("salaryPeriod") or "").lower()
            if period in {"hourly", "monthly", "yearly"}:
                source_data["salary_period"] = period

            results.append(RawJob(
                title=job.get("jobTitle") or "",
                company=job.get("companyName") or "",
                location=job_location or "Remote",
                url=url,
                source=self.name,
                source_id=str(job.get("id") or job.get("jobSlug") or ""),
                description=strip_html(job.get("jobDescription") or ""),
                posted_at=published.isoformat(),
                source_structured_data=source_data,
            ))
        return results
