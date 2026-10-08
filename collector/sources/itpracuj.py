"""it.pracuj.pl source, Poland's largest general job board, IT section.

Same corporate group (Grupa Pracuj) and Cloudflare setup as theprotocol.it: a plain
`httpx` GET gets a "Just a moment..." challenge, and even headless Playwright
(`channel="chrome"`) gets served the same challenge. Only a non-headless (visible)
browser gets through, verified live. So, like theprotocol.it, this source uses
Playwright end to end, no login/stealth pacing (public site, no account).

Unlike theprotocol.it, the `kw` search parameter here is a genuine substring/keyword
filter, not a single-technology-tag autocomplete, "symfony developer" (4 results) is a
proper subset of bare "symfony" (11 results), verified live. So the full multi-word
`title` is passed through as-is, no first-word tag extraction needed.

Search results embed a short, truncated description preview (`jobDescription`, ends in
"..."). The full description lives on the job's detail page (`offerAbsoluteUri`), on a
*different* subdomain (www.pracuj.pl vs. it.pracuj.pl for search), and navigating there
in the same browser session, right after a search-page load, triggered a real Cloudflare
CAPTCHA challenge live (not just the usual auto-clearing "Just a moment" screen). Since
bypassing a CAPTCHA is off the table, this deliberately does NOT do a second fetch: the
truncated `jobDescription` preview from search results is used as-is. It's shorter than
ideal but real, substantive content, good enough for scoring, without the CAPTCHA risk.
"""
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from collector.base import JobSource, RawJob
from collector.location import workplace_suffix
from collector.query_matcher import query_matches

logger = logging.getLogger(__name__)

# No "praca%20zdalna;wm,home-office" (remote-only) URL segment: it.pracuj.pl is
# routed for hybrid/onsite Polish-city candidates too (see collector/runner.py's
# _POLAND_ONLY_SOURCES routing), so hardcoding remote-only here silently
# returned nothing relevant for them.
_SEARCH_URL = "https://it.pracuj.pl/praca/{kw};kw"
_POLAND_ALIASES = {"poland", "polska", "pl"}
_WORK_MODE_TOKENS = {
    "praca zdalna": "remote",
    "praca hybrydowa": "hybrid",
    "praca stacjonarna": "onsite",
}


def _read_next_data(page) -> dict | None:
    try:
        # state="attached": a <script> tag is never "visible" (wait_for_selector's
        # default), it just needs to exist in the DOM.
        page.wait_for_selector("#__NEXT_DATA__", state="attached", timeout=10_000)
        raw = page.eval_on_selector("#__NEXT_DATA__", "el => el.textContent")
    except PlaywrightTimeout:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _find_query(data: dict, query_name: str) -> dict | None:
    queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    for q in queries:
        key = q.get("queryKey")
        if key and key[0] == query_name:
            return q.get("state", {}).get("data")
    return None


class ItPracujSource(JobSource):
    # Same Grupa Pracuj / Cloudflare setup as theprotocol.it, multiple back-to-back
    # searches with zero pause triggered a live Cloudflare challenge mid-run. Opting
    # into LinkedIn's adaptive pause between searches fixes it.
    requires_stealth_pauses = True

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._playwright = None
        self._browser = None
        self._page = None
        self.last_search_diagnostics = {}

    @property
    def name(self) -> str:
        return "itpracuj"

    def __enter__(self):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(channel="chrome", headless=False)
        self._page = self._browser.new_page()
        return self

    def __exit__(self, *args):
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._browser = None
        self._playwright = None
        self._page = None
        self.last_search_diagnostics = {}

    @staticmethod
    def _url_key(url: str) -> str:
        parsed = urlsplit((url or "").strip())
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))

    def search(
        self,
        title: str,
        location: str,
        days_back: int | None = None,
        max_results: int | None = None,
        known_urls: set[str] | None = None,
    ) -> list[RawJob]:
        if location.strip().lower() not in _POLAND_ALIASES:
            self.set_search_diagnostics(source_status="skipped", reason="location_not_poland", upstream_found=0)
            return []
        if not title.strip():
            self.set_search_diagnostics(source_status="skipped", reason="empty_query", upstream_found=0)
            return []

        days = days_back if days_back is not None else self._days_back
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        url = _SEARCH_URL.format(kw=quote(title.strip()))
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        except PlaywrightTimeout:
            logger.warning(f"it.pracuj.pl search timed out for title={title!r}")
            self.set_search_diagnostics(source_status="error", source_error="search timeout", upstream_found=0)
            return []
        except Exception as exc:
            self.set_search_diagnostics(source_status="error", source_error=str(exc), upstream_found=0)
            return []

        data = _read_next_data(self._page)
        if not data:
            self.set_search_diagnostics(source_status="error", source_error="missing or blocked __NEXT_DATA__", upstream_found=0)
            return []

        job_offers = _find_query(data, "jobOffers") or {}
        grouped = job_offers.get("groupedOffers", []) if isinstance(job_offers, dict) else []
        if not isinstance(grouped, list):
            grouped = []
        total = int(job_offers.get("offersTotalCount") or len(grouped)) if isinstance(job_offers, dict) else len(grouped)
        page_size = len(grouped) or 50
        total_pages = max(1, math.ceil(total / page_size))
        pages_fetched = 1
        pagination_error = None
        for next_page in range(2, total_pages + 1):
            try:
                self._page.goto(f"{url}?page={next_page}", wait_until="domcontentloaded", timeout=20_000)
                next_data = _read_next_data(self._page)
                next_jobs = _find_query(next_data or {}, "jobOffers") or {}
                next_groups = next_jobs.get("groupedOffers", []) if isinstance(next_jobs, dict) else []
                if not isinstance(next_groups, list):
                    break
                grouped.extend(next_groups)
                pages_fetched = next_page
            except Exception as exc:
                pagination_error = str(exc)
                break

        results: list[RawJob] = []
        known_keys = {self._url_key(url) for url in (known_urls or set())}
        query_matched = date_matched = geo_matched = known_url_filtered = 0
        seen_urls: set[str] = set()
        for group in grouped:
            offers = group.get("offers") or []
            if not offers:
                continue
            offer_url = offers[0].get("offerAbsoluteUri")
            if not offer_url:
                continue
            description_preview = group.get("jobDescription") or ""
            if not query_matches(title, group.get("jobTitle") or "", description_preview):
                continue
            query_matched += 1

            pub_str = group.get("lastPublicated")
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00")) if pub_str else None
            except (ValueError, TypeError, AttributeError):
                pub_dt = None
            if not pub_dt or pub_dt < cutoff:
                continue
            date_matched += 1

            canonical_url = self._url_key(offer_url)
            if canonical_url in seen_urls:
                continue
            seen_urls.add(canonical_url)
            if known_keys and canonical_url in known_keys:
                known_url_filtered += 1
                continue

            city = offers[0].get("displayWorkplace")
            modes = {_WORK_MODE_TOKENS.get(m.lower()) for m in (group.get("workModes") or [])}
            modes.discard(None)
            location_str = f"{city}, Poland{workplace_suffix(modes)}" if city else f"Poland{workplace_suffix(modes)}"
            geo_matched += 1
            source_data = (
                {"remote": True, "remote_available": True, "remote_regions": ["Poland"]}
                if "remote" in modes
                else None
            )

            if max_results and len(results) >= max_results:
                continue

            results.append(RawJob(
                title=group.get("jobTitle", ""),
                company=group.get("companyName", ""),
                location=location_str,
                url=canonical_url,
                source=self.name,
                source_id=str(offers[0].get("partitionId") or group.get("groupId") or canonical_url),
                description=description_preview or None,
                posted_at=pub_dt.isoformat() if pub_dt else None,
                source_structured_data=source_data,
            ))
        self.set_search_diagnostics(
            upstream_found=len(grouped), query_matched=query_matched,
            date_matched=date_matched, geo_matched=geo_matched,
            known_url_filtered=known_url_filtered,
            pages_fetched=pages_fetched,
            source_status="partial" if pagination_error else "empty" if not grouped else "ok",
            source_error=pagination_error,
        )
        return results
