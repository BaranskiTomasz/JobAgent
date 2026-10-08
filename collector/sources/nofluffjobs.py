"""NoFluffJobs source, Poland/CEE tech job board, public and unauthenticated.

No Cloudflare wall here (unlike theprotocol.it/it.pracuj.pl), a plain `httpx` GET with
a realistic User-Agent works directly, verified live, so no Playwright is needed for
either search() or fetch_description().

This is an Angular Universal (SSR) app: listing and detail data are both embedded as
JSON in a `<script id="serverApp-state">` tag, Angular's TransferState mechanism. The
JSON text has a handful of HTML entities escaped (`&q;`, `&a;`, `&l;`, `&g;`) that must
be un-escaped before parsing. Keys are hashed or literal-request-path strings (Angular's
HTTP TransferState cache is keyed by request identity, not a fixed name), rather than
guess the exact key, we scan all top-level values for the shape we need.

Search results don't include the full description (only tags/salary/seniority), so a
second GET to the job's detail page is made per new posting.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from collector.base import JobSource, RawJob
from collector.location import workplace_suffix
from collector.query_matcher import query_matches
from collector.utils import strip_html

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://nofluffjobs.com/pl/{kw}"
_DETAIL_URL = "https://nofluffjobs.com/pl/job/{slug}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}
_POLAND_ALIASES = {"poland", "polska", "pl"}
_STATE_RE = re.compile(r'<script id="serverApp-state" type="application/json">(.*?)</script>', re.DOTALL)


def _parse_server_state(html: str) -> dict | None:
    m = _STATE_RE.search(html)
    if not m:
        return None
    raw = m.group(1).replace("&q;", '"').replace("&a;", "&").replace("&l;", "<").replace("&g;", ">")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _find_postings(state: dict) -> list[dict]:
    for value in state.values():
        if isinstance(value, dict):
            search_response = value.get("searchResponse")
            if isinstance(search_response, dict) and "postings" in search_response:
                return search_response.get("postings") or []
    return []


def _find_description(state: dict) -> str | None:
    for value in state.values():
        if isinstance(value, dict):
            details = value.get("details")
            if isinstance(details, dict) and "description" in details:
                return strip_html(details.get("description") or "")
    return None


_KNOWN_SALARY_CURRENCIES = {"PLN", "EUR", "USD", "GBP"}


def _extract_source_structured_data(posting: dict, description: str | None = None) -> dict:
    """NoFluffJobs' own search-result payload already discloses salary as a
    structured field (verified live against the real site), not prose, no
    reason to make Haiku re-guess it from the description later. NoFluffJobs
    payload also states the period explicitly, so it remains authoritative
    when the shortened detail description omits the salary line."""
    data: dict = {"_salary_disclosed": False}
    salary = posting.get("salary") or {}
    salary_from, salary_to, currency = salary.get("from"), salary.get("to"), salary.get("currency")
    if salary_from and salary_to and currency in _KNOWN_SALARY_CURRENCIES:
        data["salary_min"] = salary_from
        data["salary_max"] = salary_to
        data["_salary_disclosed"] = True
        data["salary_currency"] = currency
        period = str(salary.get("period") or "").lower()
        if period in {"hour", "hourly"}:
            data["salary_period"] = "hourly"
        elif period in {"month", "monthly"}:
            data["salary_period"] = "monthly"
        else:
            hourly = bool(re.search(r"(?:PLN|EUR|USD|GBP)\s*/\s*(?:h|hour|godz)", description or "", re.IGNORECASE))
            data["salary_period"] = "hourly" if hourly else "monthly"
    return data


class NoFluffJobsSource(JobSource):
    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self.last_search_diagnostics = {}

    @property
    def name(self) -> str:
        return "nofluffjobs"

    def __enter__(self):
        self._client = httpx.Client(headers=_HEADERS, timeout=30, follow_redirects=True)
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self.last_search_diagnostics = {}

    @staticmethod
    def _url_key(url: str) -> str:
        parsed = urlsplit((url or "").strip())
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))

    def fetch_description(self, url: str) -> str | None:
        try:
            resp = self._client.get(url)
        except Exception as e:
            logger.warning(f"NoFluffJobs detail fetch failed: {e}")
            return None
        if resp.status_code != 200:
            return None
        state = _parse_server_state(resp.text)
        if not state:
            return None
        return _find_description(state)

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

        # No "remote=remote" criteria: NoFluffJobs is routed for hybrid/onsite
        # Polish-city candidates too (see collector/runner.py's
        # _POLAND_ONLY_SOURCES routing), so a hardcoded remote-only filter
        # silently returned nothing relevant for them.
        url = _SEARCH_URL.format(kw=quote(title.strip()))
        try:
            resp = self._client.get(url)
        except Exception as e:
            logger.warning(f"NoFluffJobs search request failed: {e}")
            self.set_search_diagnostics(source_status="error", source_error=str(e), upstream_found=0)
            return []
        if resp.status_code != 200:
            self.set_search_diagnostics(source_status="error", source_error=f"HTTP {resp.status_code}", upstream_found=0)
            return []

        state = _parse_server_state(resp.text)
        if not state:
            self.set_search_diagnostics(source_status="error", source_error="missing or blocked TransferState", upstream_found=0)
            return []

        postings = _find_postings(state)
        response_data = next((value.get("searchResponse") for value in state.values() if isinstance(value, dict) and isinstance(value.get("searchResponse"), dict)), {})
        page_info = response_data.get("pagination") or response_data.get("page") or {}
        total_pages = int(page_info.get("totalPages") or page_info.get("pages") or 1) if isinstance(page_info, dict) else 1
        pagination_error = None
        pages_fetched = 1
        for next_page in range(2, max(1, total_pages) + 1):
            try:
                page_resp = self._client.get(f"{url}?page={next_page}")
                if page_resp.status_code != 200:
                    raise RuntimeError(f"HTTP {page_resp.status_code} on page {next_page}")
                page_state = _parse_server_state(page_resp.text)
                next_postings = _find_postings(page_state or {})
                if not next_postings:
                    break
                postings.extend(next_postings)
                pages_fetched = next_page
            except Exception as exc:
                pagination_error = str(exc)
                break

        results: list[RawJob] = []
        known_keys = {self._url_key(url) for url in (known_urls or set())}
        query_matched = date_matched = geo_matched = known_url_filtered = 0
        detail_attempted = detail_failed = 0
        seen_urls: set[str] = set()
        for posting in postings:
            slug = posting.get("url")
            if not slug:
                continue
            technologies = posting.get("technology") or posting.get("technologies") or posting.get("tags") or []
            if not query_matches(title, posting.get("title") or "", " ".join(str(tag) for tag in technologies)):
                continue
            query_matched += 1

            posted_ms = posting.get("posted")
            try:
                posted_dt = datetime.fromtimestamp(posted_ms / 1000, tz=timezone.utc) if posted_ms else None
            except (TypeError, ValueError, OSError):
                posted_dt = None
            if not posted_dt or posted_dt < cutoff:
                continue
            date_matched += 1

            job_url = _DETAIL_URL.format(slug=slug)
            canonical_url = self._url_key(job_url)
            if canonical_url in seen_urls:
                continue
            seen_urls.add(canonical_url)
            if known_keys and canonical_url in known_keys:
                known_url_filtered += 1
                continue

            # "places" mixes a "Remote" pseudo-city with real cities and, for a
            # nationwide remote posting, every Polish province as separate
            # entries, take the real city (if any) for the label and treat
            # a "Remote" entry as the remote-mode signal. NoFluffJobs' listing
            # payload doesn't distinguish hybrid from onsite, so a posting with
            # a real city and no "Remote" entry is labeled with no suffix
            # rather than guessing.
            places = (posting.get("location") or {}).get("places") or []
            cities = [p.get("city") for p in places if p.get("city")]
            modes = {"remote"} if "Remote" in cities else set()
            real_city = next((c for c in cities if c != "Remote"), None)
            location = f"{real_city}, Poland{workplace_suffix(modes)}" if real_city else f"Poland{workplace_suffix(modes)}"
            geo_matched += 1

            if max_results and len(results) >= max_results:
                continue
            detail_attempted += 1
            description = self.fetch_description(job_url)
            if description is None:
                detail_failed += 1
            source_data = _extract_source_structured_data(posting, description)
            if "remote" in modes:
                source_data.update({
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": ["Poland"],
                })
            results.append(RawJob(
                title=posting.get("title", ""),
                company=posting.get("name", ""),
                location=location,
                url=canonical_url,
                source=self.name,
                source_id=posting.get("id") or canonical_url,
                description=description,
                posted_at=posted_dt.isoformat() if posted_dt else None,
                source_structured_data=source_data or None,
            ))
        self.set_search_diagnostics(
            upstream_found=len(postings), query_matched=query_matched,
            date_matched=date_matched, geo_matched=geo_matched,
            known_url_filtered=known_url_filtered,
            pages_fetched=pages_fetched,
            source_status="partial" if pagination_error else "empty" if not postings else "ok",
            source_error=pagination_error, detail_attempted=detail_attempted,
            detail_failed=detail_failed,
        )
        return results
