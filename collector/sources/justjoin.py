"""justjoin.it source, Poland-focused IT job board, public and unauthenticated.

Search results are fetched with a plain HTTP GET: the listing data (title, company,
salary, skills, workplace type) is embedded as structured JSON inside the server-rendered
page (verified live against the real site), so no browser is needed for search(). Full
descriptions aren't in that listing payload, so each new job's detail page is rendered
with Playwright to read the description text, the description is itself streamed inside
Next.js's internal RSC wire format, and reverse-engineering that byte-for-byte proved too
fragile (unstable across page loads) to rely on for real content.

This is a Poland-only board, search() only fires anything when `location` resolves to
Poland, to avoid firing the same query once per configured country for no reason.
"""
import json
import re
import logging
from datetime import datetime, timedelta, timezone

import httpx
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from collector.base import JobSource, RawJob
from collector.location import workplace_suffix

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://justjoin.it/job-offers/all-locations"
_DETAIL_URL = "https://justjoin.it/job-offer/{slug}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}
_POLAND_ALIASES = {"poland", "polska", "pl"}


def _parse_rsc_offers(html: str) -> list[dict]:
    """Extract the job-listing array embedded in the page's Next.js RSC payload.

    Each `self.__next_f.push([<id>, "<json>"])` script tag is a self-contained JSON
    array; the second element is a JSON-escaped string containing one or more
    newline-separated `<hex_id>:<json>` rows (React Server Components "rows"). It
    used to be one row per push call, but justjoin.it started bundling many rows
    (95+, verified live) into a single push, so `<hex_id>:<json>` is no longer
    "everything after the first colon" for the whole chunk - each line has to be
    split and parsed independently. We only need the one row that carries the
    dehydrated react-query state with the OFFERS listing (other row kinds, e.g.
    text/hint rows, use a different micro-syntax we don't need to understand).
    """
    for m in re.finditer(r"self\.__next_f\.push\(", html):
        start = m.end() - 1
        depth = 0
        i = start
        while i < len(html):
            if html[i] == "(":
                depth += 1
            elif html[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        arg_str = html[start + 1:i]
        try:
            arr = json.loads(arg_str)
        except Exception:
            continue
        if not (isinstance(arr, list) and len(arr) == 2 and isinstance(arr[1], str)):
            continue
        chunk = arr[1]
        if '"companyName"' not in chunk or '"employmentTypes"' not in chunk:
            continue

        for line in chunk.split("\n"):
            if '"queryKey"' not in line or "OFFERS" not in line:
                continue
            try:
                colon_idx = line.index(":")
                row = json.loads(line[colon_idx + 1:])
                queries = row[3]["state"]["queries"]
            except Exception:
                continue

            for q in queries:
                key = q.get("queryKey")
                if key and key[0] == "OFFERS":
                    try:
                        pages = q["state"]["data"]["pages"]
                    except (KeyError, TypeError):
                        continue
                    offers = []
                    for page in pages:
                        offers.extend(page.get("data", []))
                    if offers:
                        return offers
    return []


# extractor/runner.py's schema, same keys/enums, so this can be overlaid
# directly onto Haiku's output with no translation step.
_SALARY_UNIT_TO_PERIOD = {"month": "monthly", "hour": "hourly", "year": "yearly"}
_KNOWN_SALARY_CURRENCIES = {"PLN", "EUR", "USD", "GBP"}
_KNOWN_SENIORITY_LEVELS = {"junior", "mid", "senior", "lead", "director"}


def _extract_source_structured_data(offer: dict) -> dict:
    """Fields justjoin.it's own API already discloses structurally (verified
    live against the real site), salary and skills are collected here as
    UI-driven fields, not prose, so there's no reason to make Haiku re-guess
    them from the description later. Conservative like every dealbreaker
    check: skip a field entirely rather than write a value outside the
    extraction schema's own enum (e.g. an unsupported currency, or a
    non-standard salary unit)."""
    data: dict = {"_salary_disclosed": False}

    seniority = offer.get("experienceLevel")
    if seniority in _KNOWN_SENIORITY_LEVELS:
        data["seniority"] = seniority

    employment_types = offer.get("employmentTypes") or []
    # Multiple entries exist per posting: one "original" (what the company
    # actually typed in) plus several currency-converted duplicates, only the
    # original is a real disclosed figure, not justjoin's own FX estimate.
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
    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self._client: httpx.Client | None = None
        self._playwright = None
        self._browser = None
        self._page = None

    @property
    def name(self) -> str:
        return "justjoin"

    def __enter__(self):
        self._client = httpx.Client(headers=_HEADERS, timeout=30, follow_redirects=True)
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(channel="chrome", headless=True)
        self._page = self._browser.new_page()
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._client = None
        self._browser = None
        self._playwright = None
        self._page = None

    def fetch_description(self, url: str) -> str | None:
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        except PlaywrightTimeout:
            return None

        try:
            self._page.wait_for_selector(".editor-paragraph", timeout=6_000)
            paras = self._page.eval_on_selector_all(
                ".editor-paragraph",
                "els => els.map(e => e.textContent.trim()).filter(Boolean)",
            )
            text = "\n".join(paras).strip()
            if text:
                return text
        except PlaywrightTimeout:
            pass
        except Exception:
            pass

        # Some postings don't use the rich-text-editor markup above (verified live,
        # a real posting rendered its description as plain text under a "Job description"
        # heading instead). The sibling element's CSS class is MUI's auto-generated
        # hash (not stable across deploys), but the heading-text + next-sibling
        # structure is, so fall back to that instead of giving up.
        try:
            fallback = self._page.evaluate("""
                () => {
                    const headings = document.querySelectorAll('h1,h2,h3,h4');
                    for (const el of headings) {
                        if ((el.textContent || '').trim().toLowerCase() === 'job description') {
                            const sib = el.nextElementSibling;
                            return sib ? sib.textContent.trim() : '';
                        }
                    }
                    return '';
                }
            """)
        except Exception:
            return None
        return fallback or None

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

        # No "workplace" filter: justjoin.it is routed for hybrid/onsite Polish-city
        # candidates too (see collector/runner.py's _POLAND_ONLY_SOURCES routing),
        # so hardcoding remote-only here silently returned nothing relevant for them.
        try:
            resp = self._client.get(_SEARCH_URL, params={"keyword": title})
        except Exception as e:
            logger.warning(f"justjoin.it request failed: {e}")
            return []
        if resp.status_code != 200:
            return []

        offers = _parse_rsc_offers(resp.text)
        if not offers:
            return []

        results: list[RawJob] = []
        for offer in offers:
            if max_results and len(results) >= max_results:
                break

            slug = offer.get("slug")
            if not slug:
                continue

            pub_str = offer.get("publishedAt") or offer.get("lastPublishedAt")
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00")) if pub_str else None
            except ValueError:
                pub_dt = None
            if pub_dt and pub_dt < cutoff:
                continue

            url = _DETAIL_URL.format(slug=slug)
            if known_urls and url in known_urls:
                continue

            city = offer.get("city") or "Poland"
            modes = {offer.get("workplaceType")} if offer.get("workplaceType") else set()
            results.append(RawJob(
                title=offer.get("title", ""),
                company=offer.get("companyName", ""),
                location=f"{city}, Poland{workplace_suffix(modes)}",
                url=url,
                source=self.name,
                source_id=offer.get("guid"),
                description=self.fetch_description(url),
                posted_at=pub_dt.isoformat() if pub_dt else None,
                source_structured_data=_extract_source_structured_data(offer) or None,
            ))

        return results
