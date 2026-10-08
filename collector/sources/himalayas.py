"""Himalayas.app source, public RSS feed, no auth required.

Unlike weworkremotely.py's feed, this one's own pubDate is trustworthy (verified
live: correctly descending, matching the site's own lastBuildDate), so no
workaround is needed for the date filter here. The feed can carry per-job country
and timezone restrictions. Country restrictions are applied before returning a
result; timezone restrictions are preserved as source-native structured data for
downstream eligibility checks.

The feed returns a bounded window of recent postings across every category, not
a complete archive.
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import job_matches_query
from collector.utils import strip_html

_FEED_URL = "https://himalayas.app/jobs/rss"
_NS = "{https://himalayas.app/ns/jobs}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"


class HimalayasSource(JobSource):
    requires_stealth_pauses = False

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._feed_cache: list | None = None
        self._feed_error: str | None = None

    @property
    def name(self) -> str:
        return "himalayas"

    def __enter__(self):
        self._feed_cache = None
        self._feed_error = None
        return self

    def __exit__(self, *_):
        self._feed_cache = None
        self._feed_error = None

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
        except Exception as exc:
            self._feed_error = f"feed request failed: {exc}"
            self._feed_cache = []
            return []
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            self._feed_error = f"feed XML invalid: {exc}"
            self._feed_cache = []
            return []
        channel = root.find("channel")
        if channel is None:
            self._feed_error = "feed XML has no channel"
            self._feed_cache = []
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
        keyword = title.strip()

        results: list[RawJob] = []

        items = self._fetch_items()
        query_matched = date_matched = geo_matched = known_url_filtered = 0
        for item in items:
            link = (item.findtext("link") or item.findtext("guid") or "").strip()
            if not link:
                continue

            job_title = (item.findtext("title") or "").strip()
            categories = [c.text.strip() for c in item.findall("category") if c.text]
            description = strip_html(
                item.findtext(f"{_CONTENT_NS}encoded") or item.findtext("description") or ""
            ) or ""
            if keyword and not job_matches_query(
                keyword, job_title, " ".join(categories) + "\n" + description
            ):
                continue
            query_matched += 1

            pub_date_str = item.findtext("pubDate") or ""
            pub_dt = None
            if pub_date_str:
                try:
                    pub_dt = parsedate_to_datetime(pub_date_str)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError, OverflowError):
                    pub_dt = None
            if cutoff and (pub_dt is None or pub_dt < cutoff):
                continue

            expiry_str = item.findtext(f"{_NS}expiryDate") or ""
            if expiry_str:
                try:
                    expiry_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                    if expiry_dt.tzinfo is None:
                        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                    if expiry_dt < datetime.now(timezone.utc):
                        continue
                except ValueError:
                    pass
            date_matched += 1

            restrictions = [
                (node.text or "").strip()
                for node in item.findall(f"{_NS}locationRestriction")
                if (node.text or "").strip()
            ]
            candidate_location = ", ".join(restrictions)
            if candidate_location and not location_matches(candidate_location, location):
                continue
            geo_matched += 1

            company = (item.findtext(f"{_NS}companyName") or "").strip()
            timezones = [
                (node.text or "").strip()
                for node in item.findall(f"{_NS}timezoneRestriction")
                if (node.text or "").strip()
            ]
            if link in known_urls:
                known_url_filtered += 1
                continue
            if max_results and len(results) >= max_results:
                continue

            results.append(RawJob(
                title=job_title,
                company=company,
                location=candidate_location or "Remote",
                url=link,
                source=self.name,
                source_id=_source_id(link),
                description=description,
                posted_at=pub_dt.isoformat() if pub_dt else None,
                source_structured_data={
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": restrictions,
                    **({"timezone_requirement": ", ".join(timezones)} if timezones else {}),
                },
            ))
        diagnostics = {
            "upstream_found": len(items), "query_matched": query_matched,
            "date_matched": date_matched, "geo_matched": geo_matched,
            "known_url_filtered": known_url_filtered,
        }
        if self._feed_error:
            diagnostics.update(source_status="error", source_error=self._feed_error)
        self.set_search_diagnostics(**diagnostics)
        return results


def _source_id(url: str) -> str:
    parsed = urlsplit(url.strip())
    clean_path = parsed.path.rstrip("/")
    canonical = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), clean_path, "", ""))
    slug = clean_path.rsplit("/", 1)[-1]
    return slug or canonical
