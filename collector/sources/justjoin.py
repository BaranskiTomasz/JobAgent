"""justjoin.it source backed by its public candidate API.

This is a Poland-only board. Search results are paginated in full because the
upstream API does not provide a reliable publication-date ordering.
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from collector.base import JobSource, RawJob
from collector.location import workplace_suffix
from collector.query_matcher import job_matches_query_with_focused_description
from collector.utils import strip_html

logger = logging.getLogger(__name__)

_LISTING_URL = "https://justjoin.it/api/candidate-api/offers"
_DETAIL_API_URL = "https://justjoin.it/api/candidate-api/offers/{slug}"
_DETAIL_URL = "https://justjoin.it/job-offer/{slug}"
_PAGE_SIZE = 100
_MAX_PAGES = 150
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}
_POLAND_ALIASES = {"poland", "polska", "pl"}


# extractor/runner.py's schema, same keys/enums, so this can be overlaid
# directly onto Haiku's output with no translation step.
_SALARY_UNIT_TO_PERIOD = {"month": "monthly", "hour": "hourly", "year": "yearly"}
_KNOWN_SALARY_CURRENCIES = {"PLN", "EUR", "USD", "GBP"}
_KNOWN_SENIORITY_LEVELS = {"junior", "mid", "senior", "lead", "director"}


def _extract_source_structured_data(offer: dict) -> dict:
    """Return fields justjoin.it exposes natively in its offer objects."""
    data: dict = {"_salary_disclosed": False}

    seniority = offer.get("experienceLevel")
    if seniority in _KNOWN_SENIORITY_LEVELS:
        data["seniority"] = seniority

    employment_types = offer.get("employmentTypes") or []
    contract = next((e for e in employment_types if e.get("currencySource") == "original"), None)
    if contract is None and employment_types:
        contract = employment_types[0]
    if contract:
        period = _SALARY_UNIT_TO_PERIOD.get(contract.get("unit"))
        currency = contract.get("currency")
        salary_from, salary_to = contract.get("from"), contract.get("to")
        if period and currency in _KNOWN_SALARY_CURRENCIES and salary_from and salary_to:
            data["salary_min"] = round(salary_from)
            data["salary_max"] = round(salary_to)
            data["_salary_disclosed"] = True
            data["salary_currency"] = currency
            data["salary_period"] = period

    required = offer.get("requiredSkills") or []
    if required:
        data["stack_required"] = required
    preferred = offer.get("niceToHaveSkills") or []
    if preferred:
        data["stack_preferred"] = preferred
    return data


class JustJoinSource(JobSource):
    def __init__(self, days_back: int = 7, catalog: bool = False, **_):
        self._days_back = days_back
        self._catalog = catalog
        self._client: httpx.Client | None = None
        self._listings_cache: tuple[list[dict], int, str | None] | None = None
        self.last_search_diagnostics = {}

    @property
    def name(self) -> str:
        return "justjoin"

    def __enter__(self):
        self._client = httpx.Client(headers=_HEADERS, timeout=30, follow_redirects=True)
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        self._client = None
        self._listings_cache = None
        self.last_search_diagnostics = {}

    @staticmethod
    def _url_key(url: str) -> str:
        parsed = urlsplit((url or "").strip())
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")
        )

    def _fetch_listings(self) -> tuple[list[dict], int, str | None]:
        # collector/runner.py calls search() once per catalog query (18 for
        # a full catalog run) within one `with source:` session - the
        # listing set doesn't depend on the query, so without this cache
        # every query would re-paginate the ~10k-listing API from scratch.
        # A live run with the full query list timed out at 600s before this
        # cache was added (confirmed: 18x repagination of the same data).
        if self._listings_cache is not None:
            return self._listings_cache
        result = self._fetch_listings_uncached()
        self._listings_cache = result
        return result

    def _fetch_listings_uncached(self) -> tuple[list[dict], int, str | None]:
        offers: list[dict] = []
        cursor = 0
        pages_fetched = 0
        seen_cursors: set[int] = set()

        while pages_fetched < _MAX_PAGES:
            if cursor in seen_cursors:
                return offers, pages_fetched, f"repeated pagination cursor {cursor}"
            seen_cursors.add(cursor)
            try:
                response = self._client.get(
                    _LISTING_URL, params={"from": cursor, "itemsCount": _PAGE_SIZE}
                )
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                payload = response.json()
            except Exception as exc:
                return offers, pages_fetched, str(exc)

            page = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(page, list):
                return offers, pages_fetched, "listing response missing data array"
            offers.extend(offer for offer in page if isinstance(offer, dict))
            pages_fetched += 1

            if len(page) < _PAGE_SIZE:
                return offers, pages_fetched, None
            next_cursor = ((payload.get("meta") or {}).get("next") or {}).get("cursor")
            if next_cursor is None:
                return offers, pages_fetched, None
            try:
                cursor = int(next_cursor)
            except (TypeError, ValueError):
                return offers, pages_fetched, f"invalid next pagination cursor {next_cursor!r}"

        return offers, pages_fetched, f"pagination safety cap of {_MAX_PAGES} pages reached"

    def _fetch_description(self, slug: str) -> str | None:
        try:
            response = self._client.get(_DETAIL_API_URL.format(slug=slug))
            if response.status_code != 200:
                logger.warning("justjoin.it detail request for %s returned HTTP %s", slug, response.status_code)
                return None
            detail = response.json()
        except Exception as exc:
            logger.warning("justjoin.it detail request for %s failed: %s", slug, exc)
            return None
        return strip_html(detail.get("body") or "") if isinstance(detail, dict) else None

    def fetch_description(self, url: str) -> str | None:
        # collector/runner.py's backfill path (_fetch_one) calls this
        # generically by URL, with its own retry-after-delay, whenever a
        # RawJob's description came back empty from search() - e.g. this
        # listing's detail request failed that time. Without this override,
        # JobSource's no-op default silently defeats that retry for every
        # justjoin.it listing.
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return self._fetch_description(slug) if slug else None

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

        days = days_back if days_back is not None else self._days_back
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        offers, pages_fetched, pagination_error = self._fetch_listings()
        if pagination_error and not pages_fetched:
            self.set_search_diagnostics(
                source_status="error", source_error=pagination_error, upstream_found=0,
                pages_fetched=pages_fetched,
            )
            return []

        results: list[RawJob] = []
        known_keys = {self._url_key(url) for url in (known_urls or set())}
        query_matched = date_matched = geo_matched = known_url_filtered = 0
        for offer in offers:
            slug = offer.get("slug")
            if not slug:
                continue
            offer_title = offer.get("title") or ""
            skills = " ".join(
                str(skill) for skill in (offer.get("requiredSkills") or []) + (offer.get("niceToHaveSkills") or [])
            )
            if not job_matches_query_with_focused_description(title, offer_title, skills):
                continue
            query_matched += 1

            pub_str = offer.get("publishedAt") or offer.get("lastPublishedAt")
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00")) if pub_str else None
            except (ValueError, TypeError, AttributeError):
                pub_dt = None
            if not pub_dt:
                continue
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt < cutoff:
                continue
            date_matched += 1

            url = _DETAIL_URL.format(slug=slug)
            if self._url_key(url) in known_keys:
                known_url_filtered += 1
                continue

            modes = {str(offer.get("workplaceType")).casefold()} if offer.get("workplaceType") else set()
            if self._catalog and "remote" not in modes:
                continue
            geo_matched += 1
            if max_results and len(results) >= max_results:
                continue

            source_data = _extract_source_structured_data(offer)
            if "remote" in modes:
                # workplaceType=="remote" is a disclosed fact worth keeping
                # (saves Haiku from re-deriving it from the description), but
                # NOT evidence of which country the role is open to - a real
                # listing with workplaceType=="remote" had city "Stockholm
                # City" (verified live). Do not add remote_regions here: the
                # gate (extractor/runner.py) treats any PL/BG name inside
                # remote_regions as outright eligibility, bypassing the real
                # evidence check - hardcoding "Poland" there (the previous
                # behavior) would have silently let non-PL/BG-eligible
                # "remote" listings through as eligible=True.
                source_data.update({"remote": True, "remote_available": True})
            city = offer.get("city") or "Poland"
            results.append(RawJob(
                title=offer_title,
                company=offer.get("companyName", ""),
                location=f"{city}, Poland{workplace_suffix(modes)}",
                url=url,
                source=self.name,
                source_id=offer.get("guid") or self._url_key(url),
                description=self._fetch_description(slug),
                posted_at=pub_dt.isoformat(),
                source_structured_data=source_data or None,
            ))

        source_error = None
        if pagination_error:
            source_error = f"pagination failed after {pages_fetched} pages: {pagination_error}"
        self.set_search_diagnostics(
            source_status="partial" if pagination_error else "empty" if not offers else "ok",
            source_error=source_error,
            upstream_found=len(offers),
            query_matched=query_matched,
            date_matched=date_matched,
            geo_matched=geo_matched,
            known_url_filtered=known_url_filtered,
            pages_fetched=pages_fetched,
        )
        return results
