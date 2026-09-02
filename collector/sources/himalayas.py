"""Himalayas.app source, public RSS feed, no auth required.

Unlike weworkremotely.py's feed, this one's own pubDate is trustworthy (verified
live: correctly descending, matching the site's own lastBuildDate), so no
workaround is needed for the date filter here. The feed has no per-job location
field though (only a himalayasJobs:timezoneRestriction list of UTC offsets, not
worth mapping to country names since no other worldwide-remote source here does
that either), so every job is treated as open to any candidate location, same as
remotive.py/remoteok.py already do when a job's own location field is empty.

The feed itself only ever returns its ~20 most recent postings across every
category site-wide (not just tech), covering roughly the last hour at Himalayas'
posting volume - a real, unfixable scarcity, not a bug: the same category of
limitation as remoteok.io's/weworkremotely.py's small windows.
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from collector.base import JobSource, RawJob
from collector.utils import strip_html

_FEED_URL = "https://himalayas.app/jobs/rss"
_NS = "{https://himalayas.app/ns/jobs}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"


class HimalayasSource(JobSource):
    requires_stealth_pauses = False

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._feed_cache: list | None = None

    @property
    def name(self) -> str:
        return "himalayas"

    def __enter__(self):
        self._feed_cache = None
        return self

    def __exit__(self, *_):
        self._feed_cache = None

    def login(self) -> None:
        pass

    def _fetch_items(self) -> list:
        if self._feed_cache is not None:
            return self._feed_cache
        try:
            response = httpx.get(
                _FEED_URL,
                headers={"User-Agent": "JobAgent/1.0"},
                timeout=15,
                follow_redirects=True,
            )
            response.raise_for_status()
        except Exception:
            return []
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            return []
        channel = root.find("channel")
        if channel is None:
            return []
        self._feed_cache = channel.findall("item")
        return self._feed_cache

    def search(
        self,
        title: str,
        location: str,
        days_back: int | None = None,
        max_results: int | None = None,
        known_urls: set[str] | None = None,
    ) -> list[RawJob]:
        known_urls = known_urls or set()
        days = days_back if days_back is not None else self._days_back
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days) if days else None
        keyword = title.lower()

        results: list[RawJob] = []

        for item in self._fetch_items():
            link = (item.findtext("link") or item.findtext("guid") or "").strip()
            if not link or link in known_urls:
                continue

            job_title = (item.findtext("title") or "").strip()
            categories = [c.text.strip() for c in item.findall("category") if c.text]
            if keyword and keyword not in job_title.lower() and not any(
                keyword in c.lower() for c in categories
            ):
                continue

            pub_date_str = item.findtext("pubDate") or ""
            pub_dt = None
            if pub_date_str:
                try:
                    pub_dt = parsedate_to_datetime(pub_date_str)
                except Exception:
                    pub_dt = None
            if cutoff and pub_dt and pub_dt < cutoff:
                continue

            company = (item.findtext(f"{_NS}companyName") or "").strip()
            description = strip_html(item.findtext(f"{_CONTENT_NS}encoded")) or strip_html(item.findtext("description"))

            results.append(RawJob(
                title=job_title,
                company=company,
                location="Remote",
                url=link,
                source=self.name,
                source_id=link.rstrip("/").split("/")[-1],
                description=description,
                posted_at=pub_dt.isoformat() if pub_dt else None,
            ))

            if max_results and len(results) >= max_results:
                break

        return results
