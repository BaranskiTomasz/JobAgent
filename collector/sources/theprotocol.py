"""theprotocol.it source, Poland-focused IT job board, public and unauthenticated.

Unlike justjoin.it, this site sits behind Cloudflare and its headless-browser detection
is aggressive: a plain `httpx` GET gets a 403 challenge page, and even Playwright's
headless Chromium (with `channel="chrome"`) gets served the same "Just a moment..."
challenge. Only a non-headless (visible) browser gets through, verified live. So this
source uses Playwright end to end, for both search() and fetch_description(), without
any login or stealth pacing (public site, no account).

Listing and detail data are both embedded as JSON in a `<script id="__NEXT_DATA__">` tag
in the server-rendered page, no reverse-engineered wire format needed here, this is the
stable, documented Next.js pattern (unlike justjoin.it's newer RSC streaming).

The site's own search box only recognizes single technology names as filter tags (typing
a full phrase like "PHP Developer" auto-collapses to just the "PHP" tag in the UI, and a
literal multi-word keyword in the URL returns zero results), so search() uses the first
word of `title` as the tag, matching how our multi-word title criteria are all phrased
("Symfony Developer" -> "symfony", "PHP" -> "php").

As of 2026-08-30 the `/filtry/{tag}` URL no longer applies any server-side filter at
all (verified live: "python", "php", and a nonsense tag that matches nothing on the
site all returned the identical top-50 "newest offers" listing, in the same order),
so search() can no longer trust the site to have already filtered for it. Each
offer's own `technologies` array (a site-populated list of tech tags, present
regardless of the broken URL filter) is matched against the tag locally instead.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from collector.base import JobSource, RawJob
from collector.location import workplace_suffix
from collector.query_matcher import primary_query_token, query_matches

logger = logging.getLogger(__name__)

# No ";t/zdalna;rw" (remote-only) URL segment: theprotocol.it is routed for
# hybrid/onsite Polish-city candidates too (see collector/runner.py's
# _POLAND_ONLY_SOURCES routing), so hardcoding remote-only here silently
# returned nothing relevant for them.
_SEARCH_URL = "https://theprotocol.it/filtry/{tag}"
_WORK_MODE_TOKENS = {
    "zdalna": "remote", "remote": "remote",
    "hybrydowa": "hybrid", "hybrid": "hybrid",
    "stacjonarna": "onsite", "full office": "onsite",
}
_DETAIL_URL = "https://theprotocol.it/praca/{offer_url_name}"
_POLAND_ALIASES = {"poland", "polska", "pl"}


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


def _section_text(offer: dict) -> str:
    sections = offer.get("textSections") or []
    parts = [s.get("plainText", "") for s in sections if s.get("plainText")]
    return "\n\n".join(parts).strip()


# theprotocol.it shows salary as a structured field on the page (per contract type,
# a listing can offer both an employment contract and B2B, each with its own range),
# but it lives under attributes.employment.typesOfContracts, entirely separate from
# textSections, the free-text description body never mentions it. Missed here, it
# was silently invisible to extraction and the scorer treated real, disclosed pay as
# "not shown" (verified live: a 23-32k PLN/month B2B rate was in the page's own JSON
# the whole time). currencyCode comes through as the "zł" symbol, not the "PLN" code
# the extractor's schema expects, so it's normalized here.
_CURRENCY_SYMBOLS = {"zł": "PLN", "€": "EUR", "$": "USD", "£": "GBP"}


def _salary_text(offer: dict) -> str:
    contracts = (offer.get("attributes") or {}).get("employment", {}).get("typesOfContracts") or []
    lines = []
    for contract in contracts:
        salary = contract.get("salary")
        if not salary or salary.get("from") is None or salary.get("to") is None:
            continue
        currency = _CURRENCY_SYMBOLS.get(salary.get("currencyCode"), salary.get("currencyCode") or "")
        period = (salary.get("timeUnit") or {}).get("shortForm", "")
        kind = salary.get("kindCode", "")
        name = contract.get("name", "contract")
        lines.append(f"{name}: {salary['from']}-{salary['to']} {currency} per {period} ({kind})".strip())
    return "Salary: " + "; ".join(lines) if lines else ""


class TheProtocolSource(JobSource):
    # Multiple back-to-back searches with zero pause between them (the default for
    # non-LinkedIn sources) triggered a real Cloudflare challenge live, even in a
    # non-headless browser. Opting into the same adaptive pause LinkedIn uses between
    # searches fixes it, a few seconds of "look at the page" time between navigations.
    requires_stealth_pauses = True

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._playwright = None
        self._browser = None
        self._page = None

    @property
    def name(self) -> str:
        return "theprotocol"

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

    def fetch_description(self, url: str) -> str | None:
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        except PlaywrightTimeout:
            return None
        data = _read_next_data(self._page)
        if not data:
            return None
        offer = data.get("props", {}).get("pageProps", {}).get("offer")
        if not offer:
            return None
        parts = [p for p in (_salary_text(offer), _section_text(offer)) if p]
        return "\n\n".join(parts) or None

    def search(
        self,
        title: str,
        location: str,
        days_back: int | None = None,
        max_results: int | None = None,
        known_urls: set[str] | None = None,
    ) -> list[RawJob]:
        if location.strip().lower() not in _POLAND_ALIASES:
            return []

        days = days_back if days_back is not None else self._days_back
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        tag = primary_query_token(title)
        if not tag:
            return []

        url = _SEARCH_URL.format(tag=quote(tag))
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        except PlaywrightTimeout:
            logger.warning(f"theprotocol.it search timed out for tag={tag!r}")
            return []

        data = _read_next_data(self._page)
        if not data:
            return []

        offers_response = data.get("props", {}).get("pageProps", {}).get("offersResponse") or {}
        offers = offers_response.get("offers", [])
        # Only the first page (up to 50 results) is fetched, plenty for a daily
        # incremental run; deeper pagination isn't implemented yet.

        results: list[RawJob] = []
        query_matched = 0
        date_matched = 0
        for offer in offers:

            # The URL's tag no longer filters server-side (see module docstring),
            # so every offer has to be checked here regardless of which tag was
            # requested, or every query would return the same unfiltered top-50.
            technologies = offer.get("technologies") or []
            offer_title = offer.get("title") or ""
            if not query_matches(title, offer_title, " ".join(technologies)):
                continue
            query_matched += 1

            offer_url_name = offer.get("offerUrlName")
            if not offer_url_name:
                continue

            pub_str = offer.get("publicationDateUtc")
            try:
                pub_dt = datetime.fromisoformat(pub_str).replace(tzinfo=timezone.utc) if pub_str else None
            except ValueError:
                pub_dt = None
            if pub_dt and pub_dt < cutoff:
                continue
            date_matched += 1

            job_url = _DETAIL_URL.format(offer_url_name=offer_url_name)
            if known_urls and job_url in known_urls:
                continue

            workplace = offer.get("workplace") or []
            city = workplace[0].get("city") if workplace else None
            modes = {_WORK_MODE_TOKENS.get(m.lower()) for m in (offer.get("workModes") or [])}
            modes.discard(None)
            location_str = f"{city}, Poland{workplace_suffix(modes)}" if city else f"Poland{workplace_suffix(modes)}"

            if max_results and len(results) >= max_results:
                continue
            results.append(RawJob(
                title=offer.get("title", ""),
                company=offer.get("employer", ""),
                location=location_str,
                url=job_url,
                source=self.name,
                source_id=offer.get("id"),
                description=self.fetch_description(job_url),
                posted_at=pub_dt.isoformat() if pub_dt else None,
            ))

        self.set_search_diagnostics(
            upstream_found=len(offers), query_matched=query_matched,
            date_matched=date_matched, geo_matched=date_matched,
        )
        return results
