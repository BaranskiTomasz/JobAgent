from datetime import datetime, timedelta, timezone
from html import unescape
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import job_matches_query
from collector.utils import strip_html

_MAX_PAGES = 50
_BROAD_REMOTE_REGION = re.compile(
    r"\b(worldwide|global|anywhere|europe|european|emea|eu|poland|polska|bulgaria)\b",
    re.IGNORECASE,
)
_UK_RESTRICTION = re.compile(
    r"\b(uk|united kingdom|britain|british|england|scotland|wales|northern ireland)\b",
    re.IGNORECASE,
)


def _published_at(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _url_key(url: str) -> str:
    parsed = urlsplit(url.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def _is_remote(job: dict) -> bool:
    if bool(job.get("remote")):
        return True
    values = [job.get("location"), *(job.get("tags") or [])]
    return any("remote" in str(value).casefold() for value in values if value)


class ArbeitnowSource(JobSource):
    source_name = "arbeitnow"
    api_url = "https://www.arbeitnow.com/api/job-board-api"

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._jobs_cache: list[dict] | None = None
        self._fetch_diagnostics: dict[str, object] = {}

    @property
    def name(self) -> str:
        return self.source_name

    def __enter__(self):
        self._client = httpx.Client(
            headers={"User-Agent": "JobAgent/1.0 (job discovery client)", "Accept": "application/json"},
            timeout=30,
            follow_redirects=True,
        )
        self._jobs_cache = None
        self._fetch_diagnostics = {}
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._jobs_cache = None
        self._fetch_diagnostics = {}

    def _fetch_jobs(self) -> list[dict]:
        if self._jobs_cache is not None:
            return self._jobs_cache
        if self._client is None:
            raise RuntimeError("Arbeitnow source must be used as a context manager")
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._days_back)
        jobs: list[dict] = []
        seen_keys: set[str] = set()
        pages = 0
        complete = True
        for page in range(1, _MAX_PAGES + 1):
            try:
                response = self._client.get(self.api_url, params={"page": page})
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                self._fetch_diagnostics = {
                    "source_status": "partial" if jobs else "error",
                    "source_error": f"API request failed: {exc}",
                }
                complete = False
                break
            batch = data.get("data", []) if isinstance(data, dict) else []
            if not isinstance(batch, list) or not batch:
                break
            pages += 1
            for item in batch:
                if not isinstance(item, dict):
                    continue
                raw_url = str(item.get("url") or "").strip()
                key = _url_key(raw_url) if raw_url else str(item.get("slug") or repr(item))
                if key not in seen_keys:
                    seen_keys.add(key)
                    jobs.append(item)
            dates = [_published_at(item.get("created_at")) for item in batch if isinstance(item, dict)]
            dates = [value for value in dates if value is not None]
            if dates and min(dates) < cutoff:
                break
            if not (data.get("links") or {}).get("next"):
                break
        else:
            complete = False
        self._fetch_diagnostics = {
            **self._fetch_diagnostics,
            "api_pages": pages,
            "api_complete": complete,
        }
        self._jobs_cache = jobs
        return jobs

    def _location_allowed(self, job_location: str, candidate_location: str) -> bool:
        if not job_location or job_location.casefold() in {"remote", "worldwide"}:
            return True
        return location_matches(job_location, candidate_location)

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
        keyword = title.strip()
        jobs = self._fetch_jobs()
        known_keys = {_url_key(url) for url in (known_urls or set())}
        seen_keys: set[str] = set()
        results: list[RawJob] = []
        query_matched = date_matched = geo_matched = known_url_filtered = 0

        for job in jobs:
            job_title = str(job.get("title") or "").strip()
            description = strip_html(unescape(str(job.get("description") or ""))) or ""
            details = "\n".join(
                str(job.get(key) or "") for key in ("category", "tags", "skills")
            )
            if not _is_remote(job) or (
                keyword
                and not job_matches_query(keyword, job_title, details + "\n" + description)
            ):
                continue
            query_matched += 1
            published = _published_at(job.get("created_at"))
            if cutoff and (published is None or published < cutoff):
                continue
            date_matched += 1
            url = str(job.get("url") or "").strip()
            if not url:
                continue
            url_key = _url_key(url)
            if url_key in known_keys:
                known_url_filtered += 1
                continue
            if url_key in seen_keys:
                continue
            job_location = str(job.get("location") or "").strip()
            if not self._location_allowed(job_location, location):
                continue
            geo_matched += 1
            if max_results and len(results) >= max_results:
                continue
            results.append(RawJob(
                title=job_title,
                company=str(job.get("company_name") or ""),
                location=job_location or "Remote",
                url=url,
                source=self.name,
                source_id=str(job.get("slug") or url_key),
                description=description,
                posted_at=published.isoformat() if published else None,
                source_structured_data={
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": [job_location] if job_location else [],
                    "_salary_disclosed": False,
                    **(
                        {"visa_sponsorship": bool(job["visa_sponsorship"])}
                        if job.get("visa_sponsorship") is not None
                        else {}
                    ),
                },
            ))
            seen_keys.add(url_key)
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


class ArbeitnowUKSource(ArbeitnowSource):
    source_name = "arbeitnow_uk"
    api_url = "https://www.arbeitnow.co.uk/api/job-board-api"

    def _location_allowed(self, job_location: str, candidate_location: str) -> bool:
        if not job_location or _UK_RESTRICTION.search(job_location):
            return False
        if not _BROAD_REMOTE_REGION.search(job_location):
            return False
        return location_matches(job_location, candidate_location)
