"""solid.jobs source, Poland-focused IT job board built around transparent salary
ranges, public and unauthenticated.

The site is an Angular SPA and calls a clean REST API for both listing and detail data,
but a bare request to either endpoint 404s ("API endpoint nie istnieje"), the API
content-negotiates on a vendor-specific `Accept` header that a plain browser UA alone
doesn't imply. Adding the right `Accept` value (found by inspecting the real request the
SPA makes) is enough; no Playwright is needed for either search() or fetch_description(),
verified live:

- listing:  GET /api/offers?division=it&sortOrder=default
            Accept: application/vnd.solidjobs.jobofferlist+json, application/json, text/plain, */*
- detail:   GET /api/offers/{id}/{jobOfferUrl}
            Accept: application/vnd.solidjobs.jobofferdetails+json, application/json, text/plain, */*

The listing endpoint doesn't support server-side keyword search (confirmed, no query
param changes the result set) or a `division=it` recency filter, so the *entire* IT
division (1500+ offers) is fetched once per collection run and cached, then filtered
client-side per title/remote-mode/date, same shape as remotive.py's per-source cache,
just keyed by nothing (there's only one list to fetch) rather than by search term.
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from collector.base import JobSource, RawJob
from collector.location import workplace_suffix
from collector.query_matcher import query_matches
from collector.utils import strip_html

logger = logging.getLogger(__name__)

_LIST_URL = "https://solid.jobs/api/offers"
_DETAIL_URL = "https://solid.jobs/api/offers/{id}/{slug}"
_PAGE_URL = "https://solid.jobs/offer/{id}/{slug}"
_LIST_ACCEPT = "application/vnd.solidjobs.jobofferlist+json, application/json, text/plain, */*"
_DETAIL_ACCEPT = "application/vnd.solidjobs.jobofferdetails+json, application/json, text/plain, */*"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}
_POLAND_ALIASES = {"poland", "polska", "pl"}
# solid.jobs is routed for hybrid/onsite Polish-city candidates too (see
# collector/runner.py's _POLAND_ONLY_SOURCES routing), a remote-only filter
# here silently returned nothing relevant for them (verified live: "Hybrydowo"
# and "Możliwa częściowo" alone account for more offers than the old
# remote-only allowlist). Every remotePossible value observed live is mapped
# below instead of filtering any of them out.
_REMOTE_MODE_TOKENS = {
    "w całości": "remote", "możliwa w całości": "remote",
    "dowolnie": "remote", "stacjonarnie lub zdalnie": "remote",
    "hybrydowo": "hybrid", "możliwa częściowo": "hybrid",
    "brak": "onsite",
}


class SolidJobsSource(JobSource):
    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._offers_cache: list[dict] | None = None
        self._fetch_error: str | None = None
        self.last_search_diagnostics = {}

    @property
    def name(self) -> str:
        return "solidjobs"

    def __enter__(self):
        self._client = httpx.Client(headers=_HEADERS, timeout=30, follow_redirects=True)
        self._offers_cache = None
        self._fetch_error = None
        self.last_search_diagnostics = {}
        return self

    @staticmethod
    def _url_key(url: str) -> str:
        parsed = urlsplit((url or "").strip())
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._offers_cache = None
        self._fetch_error = None
        self.last_search_diagnostics = {}

    def _fetch_all_offers(self) -> list[dict]:
        if self._offers_cache is not None:
            return self._offers_cache
        try:
            resp = self._client.get(
                _LIST_URL,
                params={"division": "it", "sortOrder": "default"},
                headers={"Accept": _LIST_ACCEPT},
            )
        except Exception as e:
            logger.warning(f"solid.jobs list request failed: {e}")
            self._fetch_error = str(e)
            self._offers_cache = []
            return []
        if resp.status_code != 200:
            self._fetch_error = f"HTTP {resp.status_code}"
            self._offers_cache = []
            return []
        try:
            payload = resp.json()
            self._offers_cache = payload if isinstance(payload, list) else payload.get("offers", []) if isinstance(payload, dict) else []
        except ValueError:
            self._fetch_error = "invalid JSON response"
            self._offers_cache = []
        return self._offers_cache

    def fetch_description(self, url: str) -> str | None:
        parts = url.rstrip("/").split("/")
        if len(parts) < 2:
            return None
        offer_id, slug = parts[-2], parts[-1]
        try:
            resp = self._client.get(
                _DETAIL_URL.format(id=offer_id, slug=slug),
                headers={"Accept": _DETAIL_ACCEPT},
            )
        except Exception as e:
            logger.warning(f"solid.jobs detail request failed: {e}")
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        details = data.get("jobOfferDetails") or {}
        parts_out = [
            strip_html(details.get("jobDescription", "")) or "",
            strip_html(details.get("candidateProfile", "")) or "",
        ]
        text = "\n\n".join(p for p in parts_out if p).strip()
        return text or None

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
        offers = self._fetch_all_offers()

        results: list[RawJob] = []
        known_keys = {self._url_key(url) for url in (known_urls or set())}
        query_matched = 0
        date_matched = 0
        geo_matched = 0
        detail_attempted = detail_failed = known_url_filtered = 0
        seen_urls: set[str] = set()
        for offer in offers:

            job_title = offer.get("jobTitle", "")
            skills = [s.get("name", "") for s in offer.get("requiredSkills") or []]
            if not query_matches(title, job_title, " ".join(skills)):
                continue
            query_matched += 1

            valid_from = offer.get("validFrom")
            try:
                valid_dt = datetime.fromisoformat(valid_from) if valid_from else None
                if valid_dt and valid_dt.tzinfo is None:
                    valid_dt = valid_dt.replace(tzinfo=timezone.utc)
                elif valid_dt:
                    valid_dt = valid_dt.astimezone(timezone.utc)
            except (ValueError, TypeError, AttributeError):
                valid_dt = None
            if not valid_dt or valid_dt < cutoff:
                continue
            date_matched += 1

            offer_id = offer.get("id")
            slug = offer.get("jobOfferUrl")
            if not offer_id or not slug:
                continue

            job_url = _PAGE_URL.format(id=offer_id, slug=slug)
            canonical_url = self._url_key(job_url)
            if canonical_url in seen_urls:
                continue
            seen_urls.add(canonical_url)
            if known_keys and canonical_url in known_keys:
                known_url_filtered += 1
                continue

            city = offer.get("companyCity")
            mode = _REMOTE_MODE_TOKENS.get((offer.get("remotePossible") or "").strip().lower())
            modes = {mode} if mode else set()
            location_str = f"{city}, Poland{workplace_suffix(modes)}" if city else f"Poland{workplace_suffix(modes)}"
            geo_matched += 1

            if max_results and len(results) >= max_results:
                continue
            detail_attempted += 1
            description = self.fetch_description(job_url)
            if description is None:
                detail_failed += 1
            source_data = {}
            if "remote" in modes:
                source_data = {
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": ["Poland"],
                }
            salary = offer.get("salary") if isinstance(offer.get("salary"), dict) else {}
            salary_min = salary.get("from") or offer.get("salaryMin")
            salary_max = salary.get("to") or offer.get("salaryMax")
            currency = salary.get("currency") or offer.get("salaryCurrency")
            if salary_min is not None or salary_max is not None:
                source_data["_salary_disclosed"] = True
                if salary_min is not None:
                    source_data["salary_min"] = salary_min
                if salary_max is not None:
                    source_data["salary_max"] = salary_max
                if currency:
                    source_data["salary_currency"] = currency
            results.append(RawJob(
                title=job_title,
                company=offer.get("companyName", ""),
                location=location_str,
                url=canonical_url,
                source=self.name,
                source_id=str(offer_id),
                description=description,
                posted_at=valid_dt.isoformat() if valid_dt else None,
                source_structured_data=source_data or None,
            ))

        self.set_search_diagnostics(
            upstream_found=len(offers), query_matched=query_matched,
            date_matched=date_matched, geo_matched=geo_matched,
            known_url_filtered=known_url_filtered,
            source_status="error" if self._fetch_error else "empty" if not offers else "ok",
            source_error=self._fetch_error,
            detail_attempted=detail_attempted,
            detail_failed=detail_failed,
        )
        return results
