"""We Work Remotely public RSS feeds."""
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import job_matches_query
from collector.utils import strip_html


def _region_matches(wwr_region: str, search_location: str) -> bool:
    return location_matches(wwr_region, search_location)


class WWRSource(JobSource):
    requires_stealth_pauses = False
    _FEED_URLS = (
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    )

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._feed_cache: list | None = None
        self._feed_errors: dict[str, str] = {}

    def __enter__(self):
        self._feed_cache = None
        self._feed_errors = {}
        return self

    def __exit__(self, *_):
        self._feed_cache = None
        self._feed_errors = {}

    @property
    def name(self) -> str:
        return "weworkremotely"

    def _fetch_feed(self, url: str) -> list:
        response = httpx.get(
            url,
            headers={"User-Agent": "JobAgent/1.0"},
            timeout=15,
            follow_redirects=True,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        channel = root.find("channel")
        if channel is None:
            raise ValueError(f"RSS channel missing: {url}")
        return channel.findall("item")

    def _fetch_items(self) -> list:
        if self._feed_cache is not None:
            return self._feed_cache
        items_by_url = {}
        for url in self._FEED_URLS:
            try:
                items = self._fetch_feed(url)
            except (httpx.HTTPError, ET.ParseError, ValueError) as error:
                self._feed_errors[url] = str(error)[:1000]
                continue
            for item in items:
                link = (item.findtext("link") or item.findtext("guid") or "").strip()
                if link:
                    items_by_url.setdefault(link, item)
        if not items_by_url and self._feed_errors:
            raise RuntimeError("; ".join(self._feed_errors.values()))
        self._feed_cache = list(items_by_url.values())
        return self._feed_cache

    def search(
        self,
        title: str,
        location: str,
        days_back: int | None = None,
        max_results: int | None = None,
        known_urls: set | None = None,
    ) -> list[RawJob]:
        days = days_back if days_back is not None else self._days_back
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        known_urls = known_urls or set()
        items = self._fetch_items()
        results: list[RawJob] = []
        query_matched = 0
        date_matched = 0
        geo_matched = 0
        known_url_filtered = 0

        for item in items:
            link = (item.findtext("link") or item.findtext("guid") or "").strip()
            if not link:
                continue
            raw_title = (item.findtext("title") or "").strip()
            if ": " in raw_title:
                company, job_title = raw_title.split(": ", 1)
            else:
                company, job_title = "", raw_title
            description = strip_html(item.findtext("description") or "")
            if not job_matches_query(title, job_title, description):
                continue
            query_matched += 1
            try:
                published = parsedate_to_datetime(item.findtext("pubDate") or "")
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if published < cutoff:
                continue
            date_matched += 1
            region = (item.findtext("region") or "").strip()
            if not _region_matches(region, location):
                continue
            geo_matched += 1
            if link in known_urls:
                known_url_filtered += 1
                continue
            if max_results and len(results) >= max_results:
                continue
            results.append(RawJob(
                title=job_title,
                company=company,
                location=region or "Remote",
                url=link,
                source=self.name,
                source_id=link.rstrip("/").split("/")[-1],
                description=description,
                posted_at=published.isoformat(),
                source_structured_data={
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": [region] if region else [],
                },
            ))

        diagnostics = {
            "upstream_found": len(items),
            "query_matched": query_matched,
            "date_matched": date_matched,
            "geo_matched": geo_matched,
            "known_url_filtered": known_url_filtered,
        }
        if self._feed_errors:
            diagnostics.update(
                source_status="partial",
                source_error="; ".join(self._feed_errors.values())[:1000],
            )
        self.set_search_diagnostics(**diagnostics)
        return results
