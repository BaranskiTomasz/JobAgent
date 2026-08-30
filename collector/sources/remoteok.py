"""Remote OK source, public JSON API, no auth required."""
from datetime import datetime, timedelta, timezone

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.utils import strip_html

_API_URL = "https://remoteok.io/api"


class RemoteOKSource(JobSource):
    requires_stealth_pauses = False

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        # The unauthenticated API only ever exposes its newest ~100 postings
        # site-wide (not per-category), so the plain feed alone is a tiny,
        # increasingly non-tech-dominated sample (verified live: mostly retail/
        # recruiter-spam postings on a given day). ?tags={tag} narrows the same
        # endpoint to postings carrying that tag, surfacing tech jobs the plain
        # feed's window would otherwise miss - but a real match sometimes lacks
        # the tag too (also verified live), so both are fetched and merged
        # rather than trusting either alone. Cached separately (generic feed
        # reused across every query, each tag reused across its 27 candidate-
        # country location iterations) instead of refetching per call.
        self._generic_cache: list[dict] | None = None
        self._tag_cache: dict[str, list[dict]] = {}

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
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._generic_cache = None
        self._tag_cache = {}

    def login(self) -> None:
        pass

    def _fetch_url(self, url: str) -> list[dict]:
        try:
            resp = self._client.get(url)
        except Exception:
            return []
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except Exception:
            return []
        if not isinstance(data, list) or len(data) < 2:
            return []
        # First element is API metadata, not a job, skip it
        return [item for item in data[1:] if isinstance(item, dict)]

    def _fetch_jobs(self, tag: str) -> list[dict]:
        if self._generic_cache is None:
            self._generic_cache = self._fetch_url(_API_URL)

        by_url: dict[str, dict] = {j["url"]: j for j in self._generic_cache if j.get("url")}

        if tag:
            if tag not in self._tag_cache:
                self._tag_cache[tag] = self._fetch_url(f"{_API_URL}?tags={tag}")
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
        keyword = title.lower()
        tag     = title.strip().split()[0].lower() if title.strip() else ""

        jobs = self._fetch_jobs(tag)

        results: list[RawJob] = []
        for job in jobs:
            if max_results and len(results) >= max_results:
                break

            date_str = job.get("date", "")
            try:
                pub_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except (ValueError, AttributeError):
                continue

            url = job.get("url", "")
            if not url:
                continue
            if known_urls and url in known_urls:
                continue

            position = job.get("position", "")
            if keyword and keyword not in position.lower():
                continue

            job_location = job.get("location", "") or ""
            if not location_matches(job_location, location):
                continue

            results.append(RawJob(
                title=position,
                company=job.get("company", ""),
                location=job_location or "Remote",
                url=url,
                source="remoteok",
                source_id=job.get("slug", ""),
                description=strip_html(job.get("description", "")),
                posted_at=pub_dt.isoformat(),
            ))

        return results
