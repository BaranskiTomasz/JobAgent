from datetime import datetime, timedelta, timezone
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import query_matches
from collector.utils import strip_html

_API = "https://hn.algolia.com/api/v1"


def _remote_region(text: str) -> str | None:
    lowered = text.casefold()
    if "remote" not in lowered and "work from anywhere" not in lowered:
        return None
    if any(token in lowered for token in ("worldwide", "work from anywhere", "remote anywhere", "globally remote")):
        return "Worldwide"
    for country in ("poland", "bulgaria"):
        if country in lowered:
            return country.title()
    if "emea" in lowered:
        return "EMEA"
    if "eea" in lowered:
        return "EEA"
    if "europe" in lowered:
        return "Europe"
    if re.search(r"\beu\b", lowered) and ("remote" in lowered or "work from" in lowered):
        return "EU"
    return None


def _candidate_excluded(text: str, candidate_location: str) -> bool:
    candidate = candidate_location.casefold().strip()
    if not candidate:
        return False
    aliases = {candidate}
    if candidate == "poland":
        aliases.update({"polska", "pl"})
    elif candidate == "bulgaria":
        aliases.add("bg")
    return any(
        re.search(
            rf"\b(?:except|excluding|not available in|outside)\s+(?:of\s+)?{re.escape(alias)}\b",
            text,
            re.IGNORECASE,
        )
        for alias in aliases
    )


class HackerNewsSource(JobSource):
    def __init__(self, days_back: int = 40, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._comments_cache: list[dict] | None = None
        self._thread_error: str | None = None
        self._thread_id: str | None = None

    @property
    def name(self) -> str:
        return "hackernews"

    def __enter__(self):
        self._client = httpx.Client(
            headers={"User-Agent": "JobAgent/1.0 (job discovery client)"},
            timeout=30,
            follow_redirects=True,
        )
        self._comments_cache = None
        self._thread_error = None
        self._thread_id = None
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._comments_cache = None
        self._thread_error = None
        self._thread_id = None

    def _fetch_comments(self) -> list[dict]:
        if self._comments_cache is not None:
            return self._comments_cache
        try:
            response = self._client.get(
                f"{_API}/search_by_date",
                params={"query": "Ask HN: Who is hiring?", "tags": "story", "hitsPerPage": 100},
            )
            response.raise_for_status()
            payload = response.json()
            hits = payload.get("hits", []) if isinstance(payload, dict) else []
            stories = [
                hit
                for hit in hits
                if isinstance(hit, dict)
                and (hit.get("title") or "").casefold().startswith(
                    "ask hn: who is hiring?"
                )
            ]
            if not stories:
                raise LookupError("monthly Who is hiring thread not found")
            story = max(
                stories,
                key=lambda hit: (
                    hit.get("created_at_i") or 0,
                    str(hit.get("objectID") or ""),
                ),
            )
            self._thread_id = str(story.get("objectID") or "")
            item = self._client.get(f"{_API}/items/{story['objectID']}")
            item.raise_for_status()
            item_payload = item.json()
            self._comments_cache = item_payload.get("children", []) if isinstance(item_payload, dict) else []
            if not isinstance(self._comments_cache, list):
                raise ValueError("thread children field is not a list")
        except (httpx.HTTPError, KeyError, LookupError, TypeError, ValueError) as exc:
            self._thread_error = str(exc)
            self._comments_cache = []
        return self._comments_cache

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
        known_keys = {self._url_key(url) for url in (known_urls or set())}
        query_matched = date_matched = geo_matched = known_url_filtered = 0
        for comment in self._fetch_comments():
            description = strip_html(comment.get("text") or "") or ""
            if not query_matches(title, description):
                continue
            query_matched += 1
            try:
                raw_created = comment.get("created_at")
                if raw_created:
                    created = datetime.fromisoformat(
                        str(raw_created).replace("Z", "+00:00")
                    )
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                else:
                    created = datetime.fromtimestamp(
                        int(comment.get("created_at_i")), timezone.utc
                    )
            except (ValueError, AttributeError, TypeError, OverflowError, OSError):
                continue
            if created < cutoff:
                continue
            date_matched += 1
            region = _remote_region(description[:1200])
            if (
                not region
                or _candidate_excluded(description[:1200], location)
                or not location_matches(region, location)
            ):
                continue
            geo_matched += 1
            comment_id = str(comment.get("id") or comment.get("objectID") or "")
            if not comment_id:
                continue
            url = f"https://news.ycombinator.com/item?id={comment_id}"
            if known_keys and self._url_key(url) in known_keys:
                known_url_filtered += 1
                continue
            header = next((line.strip() for line in description.splitlines() if line.strip()), "")
            company = header.split("|")[0].strip()[:120] or "Unknown company"
            if max_results and len(results) >= max_results:
                continue
            results.append(RawJob(
                title=f"{title} role at {company}",
                company=company,
                location=region,
                url=url,
                source=self.name,
                source_id=comment_id,
                description=description,
                posted_at=created.isoformat(),
                source_structured_data={
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": [region],
                },
            ))
        self.set_search_diagnostics(
            thread_id=self._thread_id,
            comments_seen=len(self._comments_cache or []),
            upstream_found=len(self._comments_cache or []),
            query_matched=query_matched,
            date_matched=date_matched,
            geo_matched=geo_matched,
            known_url_filtered=known_url_filtered,
            source_status=(
                "error"
                if self._thread_error
                else "empty"
                if not self._comments_cache
                else "ok"
            ),
            source_error=self._thread_error,
        )
        return results

    @staticmethod
    def _url_key(url: str) -> str:
        parsed = urlsplit((url or "").strip())
        query = parse_qs(parsed.query)
        item_id = (query.get("id") or [""])[0]
        if item_id:
            return urlunsplit(
                ("https", "news.ycombinator.com", "/item", "", f"id={item_id}")
            )
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")
        )
