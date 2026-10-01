import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import query_tokens

_BOARDS_FILE = Path(__file__).with_name("ats_companies.json")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, AttributeError):
        return None


def _eligible_remote(location: str, description: str, candidate_location: str) -> bool:
    combined = f"{location}\n{description[:4000]}".lower()
    if not any(token in combined for token in ("remote", "work from home", "work from anywhere", "distributed team")):
        return False
    generic = location.lower().strip() in {"", "remote", "remote only", "fully remote"}
    if not generic:
        return location_matches(location, candidate_location)
    if any(token in combined for token in ("worldwide", "work from anywhere", "remote anywhere", "globally remote")):
        return True
    if location_matches("Europe", candidate_location) and re.search(r"\b(europe|emea|eea|eu)\b", combined):
        return True
    candidate = candidate_location.lower().strip()
    return bool(candidate and re.search(rf"\b{re.escape(candidate)}\b", combined))


class ATSBoardSource(JobSource):
    provider = ""

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._jobs_cache: list[RawJob] | None = None
        self._search_index: list[tuple[RawJob, set[str], set[str], datetime | None]] | None = None
        entries = json.loads(_BOARDS_FILE.read_text(encoding="utf-8"))
        self._boards = [entry for entry in entries if entry["source"] == self.provider]

    @property
    def name(self) -> str:
        return self.provider

    def __enter__(self):
        self._client = httpx.Client(
            headers={"User-Agent": "JobAgent/1.0 (job discovery client)"},
            timeout=30,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=12),
        )
        self._jobs_cache = None
        self._search_index = None
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._jobs_cache = None
        self._search_index = None

    def _fetch_board(self, board: dict) -> list[RawJob]:
        raise NotImplementedError

    def _fetch_jobs(self) -> list[RawJob]:
        if self._jobs_cache is not None:
            return self._jobs_cache
        jobs: list[RawJob] = []
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(self._fetch_board, board) for board in self._boards]
            for future in as_completed(futures):
                try:
                    jobs.extend(future.result())
                except Exception:
                    continue
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
        jobs = self._fetch_jobs()
        if self._search_index is None:
            self._search_index = [
                (
                    job,
                    set(query_tokens(job.title)),
                    set(query_tokens(f"{job.title} {job.description or ''}")),
                    _parse_iso(job.posted_at),
                )
                for job in jobs
            ]
        required = set(query_tokens(title))
        results: list[RawJob] = []
        query_matched = 0
        date_matched = 0
        geo_matched = 0
        for job, title_tokens, available, published in self._search_index:
            if not required or ("engineer" in required and "engineer" not in title_tokens) or not required.issubset(available):
                continue
            query_matched += 1
            if not published or published < cutoff:
                continue
            date_matched += 1
            if not _eligible_remote(job.location, job.description or "", location):
                continue
            geo_matched += 1
            if known_urls and job.url in known_urls:
                continue
            if not max_results or len(results) < max_results:
                results.append(job)
        self.set_search_diagnostics(
            upstream_found=len(jobs), query_matched=query_matched,
            date_matched=date_matched, geo_matched=geo_matched,
        )
        return results
