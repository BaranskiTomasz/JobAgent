from datetime import datetime, timedelta, timezone

import httpx

from collector.base import JobSource, RawJob
from collector.location import location_matches
from collector.query_matcher import query_matches
from collector.utils import strip_html

_API = "https://hn.algolia.com/api/v1"


def _remote_region(text: str) -> str | None:
    lowered = text.lower()
    if "remote" not in lowered and "work from anywhere" not in lowered:
        return None
    if any(token in lowered for token in ("worldwide", "work from anywhere", "remote anywhere", "globally remote")):
        return "Worldwide"
    if "poland" in lowered:
        return "Poland"
    if "emea" in lowered:
        return "EMEA"
    if "eea" in lowered:
        return "EEA"
    if "europe" in lowered:
        return "Europe"
    if "remote eu" in lowered or "eu remote" in lowered or "remote (eu" in lowered:
        return "EU"
    return None


class HackerNewsSource(JobSource):
    def __init__(self, days_back: int = 40, **_):
        self._days_back = max(days_back, 40)
        self._client: httpx.Client | None = None
        self._comments_cache: list[dict] | None = None

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
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._comments_cache = None

    def _fetch_comments(self) -> list[dict]:
        if self._comments_cache is not None:
            return self._comments_cache
        try:
            response = self._client.get(
                f"{_API}/search_by_date",
                params={"query": "Ask HN: Who is hiring?", "tags": "story", "hitsPerPage": 20},
            )
            response.raise_for_status()
            hits = response.json().get("hits", [])
            story = next(
                hit for hit in hits
                if (hit.get("title") or "").lower().startswith("ask hn: who is hiring?")
            )
            item = self._client.get(f"{_API}/items/{story['objectID']}")
            item.raise_for_status()
            self._comments_cache = item.json().get("children", [])
        except Exception:
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
        days = max(days_back if days_back is not None else self._days_back, 40)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        results: list[RawJob] = []
        for comment in self._fetch_comments():
            if max_results and len(results) >= max_results:
                break
            description = strip_html(comment.get("text") or "") or ""
            if not query_matches(title, description):
                continue
            region = _remote_region(description[:1200])
            if not region or not location_matches(region, location):
                continue
            try:
                created = datetime.fromisoformat(comment.get("created_at", "").replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if created < cutoff:
                continue
            comment_id = str(comment.get("id") or "")
            url = f"https://news.ycombinator.com/item?id={comment_id}"
            if known_urls and url in known_urls:
                continue
            header = next((line.strip() for line in description.splitlines() if line.strip()), "")
            company = header.split("|")[0].strip()[:120] or "Unknown company"
            results.append(RawJob(
                title=f"{title} role at {company}",
                company=company,
                location=region,
                url=url,
                source=self.name,
                source_id=comment_id,
                description=description,
                posted_at=created.isoformat(),
                source_structured_data={"remote": True, "remote_regions": [region]},
            ))
        return results
