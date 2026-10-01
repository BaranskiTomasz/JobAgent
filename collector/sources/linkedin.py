import time
import random
import os
from urllib.parse import quote
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AGENT, LINKEDIN, STEALTH
from collector.base import JobSource, RawJob
from collector.query_matcher import query_matches

_SEARCH_URL = LINKEDIN["search_url"]
_MAX_PAGES_PER_SEARCH = 10

# Pages visited between job batches to simulate organic browsing
_DISTRACTION_URLS = [
    "https://www.linkedin.com/feed/",
    "https://www.linkedin.com/mynetwork/",
    "https://www.linkedin.com/jobs/",
]


def _reading_seconds(text: str) -> float:
    """Base reading-time estimate for a description: word count / reading speed,
    with natural variance, clamped to a plausible range. Split out from the sleeping
    wrapper so the formula itself is unit-testable."""
    words = len(text.split())
    seconds = words / (STEALTH["desc_reading_wpm"] / 60) * random.uniform(0.85, 1.15)
    return max(STEALTH["desc_delay_min"], min(STEALTH["desc_delay_max"], seconds))


class LinkedInSource(JobSource):
    requires_stealth_pauses = True

    def __init__(self, days_back: int = 7):
        self._days_back = days_back
        self._playwright = None
        self._browser = None
        self._page = None

    @property
    def name(self) -> str:
        return "linkedin"

    # --- lifecycle ---

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch_persistent_context(
            user_data_dir=os.path.join(ROOT, "data", "chrome_profile"),
            channel="chrome",
            headless=AGENT["headless"],
            viewport={"width": 1280, "height": 800},
        )
        self._page = self._browser.pages[-1] if self._browser.pages else self._browser.new_page()

    def stop(self) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    # --- public API ---

    def login(self) -> None:
        """Log in to LinkedIn. Auto-fills credentials from env if available, falls back to manual."""
        self._goto("https://www.linkedin.com/login")
        if "feed" in self._page.url or "jobs" in self._page.url:
            return

        email    = os.environ.get("LINKEDIN_EMAIL", "")
        password = os.environ.get("LINKEDIN_PASSWORD", "")

        if email and password:
            print("Logging in to LinkedIn automatically...")
            try:
                self._page.fill("#username", email)
                self._page.fill("#password", password)
                self._page.click("button[type='submit']")
                self._page.wait_for_url("**/feed/**", timeout=15_000)
                print("Logged in.")
                self._wait()
                return
            except PlaywrightTimeout:
                # 2FA / checkpoint, fall through to manual wait
                print("Verification required (2FA or CAPTCHA), please complete in the browser window...")
        else:
            print("Please log in to LinkedIn in the browser window (waiting up to 10 minutes)...")

        self._page.wait_for_url("**/feed/**", timeout=600_000)
        print("Logged in.")
        self._wait()

    def search(self, title: str, location: str, days_back: int | None = None, max_results: int | None = None, known_urls: set[str] | None = None) -> list[RawJob]:
        days = days_back if days_back is not None else self._days_back
        seconds = days * 24 * 3600
        url = (
            f"{_SEARCH_URL}"
            f"?keywords={quote(title)}"
            f"&location={quote(location)}"
            f"&f_WT=2"
            f"&f_TPR=r{seconds}"
            f"&sortBy=DD"
        )
        self._goto(url)
        self._wait()
        cards = self._collect_cards(max_jobs=max_results, known_urls=known_urls)
        return [card for card in cards if query_matches(title, card.title)]

    def fetch_description(self, url: str) -> str | None:
        self._jitter_mouse()
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeout:
            return None
        try:
            self._page.wait_for_load_state("networkidle", timeout=8_000)
        except PlaywrightTimeout:
            time.sleep(3)

        self._jitter_mouse()
        self._scroll_to_bottom()
        # Occasionally scroll back up a bit, like re-reading something
        if random.random() < 0.3:
            up = random.randint(200, 600)
            self._page.evaluate(f"window.scrollBy(0, -{up})")
            time.sleep(random.uniform(1, 3))
            self._scroll_to_bottom()

        text = self._page.evaluate("""
            () => {
                // Try LinkedIn's structured description element first (prefer longer content)
                const descEl = document.querySelector('.jobs-description__content, .jobs-box__html-content, .description__text');
                const descText = descEl ? descEl.innerText.trim() : '';
                if (descText.length > 100) {
                    return descText.slice(0, 8000);
                }
                // Fall back to section header, multilingual
                const headers = [
                    'About the job', 'Über die Stelle', 'À propos du poste',
                    'Over de functie', 'Sobre el trabajo', 'O tej pracy',
                    'Om jobbet', 'Tietoja työpaikasta', 'Om stillingen',
                ];
                const body = document.body.innerText;
                for (const h of headers) {
                    const idx = body.indexOf(h);
                    if (idx !== -1) return body.slice(idx, idx + 8000).trim();
                }
                // Last resort: use whatever the structured element found, even if short
                if (descText.length > 0) return descText.slice(0, 8000);
                return '';
            }
        """) or None

        if text:
            self._reading_delay(text)
        else:
            # Nothing to read, still spend some time on the page before giving up.
            self._human_delay(STEALTH["desc_delay_min"], STEALTH["desc_delay_max"])

        return text

    def distract(self) -> None:
        """Visit a random non-job LinkedIn page to simulate organic browsing."""
        url = random.choice(_DISTRACTION_URLS)
        print(f"  [stealth] Visiting {url}")
        try:
            self._goto(url)
        except Exception:
            return
        self._jitter_mouse()
        self._scroll_to_bottom()
        self._human_delay(15, 45)

    # --- private helpers ---

    def _human_delay(self, min_sec: float, max_sec: float) -> float:
        """Beta-distributed delay: mostly short, occasionally long. 5% chance of extra 1-5 min pause."""
        r = random.betavariate(2, 5)   # peaks near 0.25 of range
        wait = min_sec + r * (max_sec - min_sec)
        if random.random() < 0.05:     # 5%, "went to make coffee"
            wait += random.uniform(60, 300)
        time.sleep(wait)
        return wait

    def _reading_delay(self, text: str) -> float:
        """Delay scaled to how long a human would take to read this specific
        description, clamped to a plausible range. Same 5% "coffee break" chance
        as _human_delay for occasional distraction."""
        wait = _reading_seconds(text)
        if random.random() < 0.05:
            wait += random.uniform(60, 300)
        time.sleep(wait)
        return wait

    def _jitter_mouse(self) -> None:
        """Move mouse to a few random positions to simulate natural cursor movement."""
        try:
            vw = self._page.viewport_size or {"width": 1280, "height": 800}
            w, h = vw["width"], vw["height"]
            steps = random.randint(2, 5)
            for _ in range(steps):
                x = random.randint(100, w - 100)
                y = random.randint(100, h - 100)
                self._page.mouse.move(x, y)
                time.sleep(random.uniform(0.1, 0.4))
        except Exception:
            pass

    def _goto(self, url: str) -> None:
        try:
            self._page.goto(url, wait_until="networkidle", timeout=30_000)
        except PlaywrightTimeout:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)

    def _wait(self, min_sec: float = 2, max_sec: float = 8) -> None:
        time.sleep(random.uniform(min_sec, max_sec))

    def _scroll_to_bottom(self) -> None:
        scroll_pos = 0
        while True:
            scroll_height = self._page.evaluate("() => document.body.scrollHeight")
            if scroll_pos >= scroll_height:
                break
            scroll_pos = min(scroll_pos + random.randint(300, 700), scroll_height)
            self._page.evaluate(f"window.scrollTo(0, {scroll_pos})")
            time.sleep(random.uniform(0.2, 0.8))

    def _collect_cards(self, max_jobs: int | None = None, known_urls: set[str] | None = None) -> list[RawJob]:
        results: list[RawJob] = []
        page_num = 0
        new_total = 0  # estimated-new cards (not in known_urls) for max_jobs cap

        while True:
            try:
                self._page.wait_for_selector(".scaffold-layout__list-item", timeout=10_000)
            except PlaywrightTimeout:
                print("  No results found or LinkedIn page structure changed.")
                break

            # When a query genuinely matches nothing, LinkedIn shows a "no results" banner
            # alongside an unrelated "Jobs you may be interested in" widget, which reuses
            # the exact same .scaffold-layout__list-item markup as real results. Without
            # this check we'd silently scrape someone's personalized recommendations.
            if self._page.query_selector(".jobs-search-no-results-banner"):
                print("  No matching jobs, LinkedIn showed unrelated recommendations instead. Skipping.")
                break

            self._scroll_to_bottom()

            cards = self._page.evaluate("""
                () => {
                    const cards = document.querySelectorAll('.scaffold-layout__list-item');
                    const results = [];
                    cards.forEach(card => {
                        const titleEl    = card.querySelector('.job-card-list__title--link');
                        const companyEl  = card.querySelector('.artdeco-entity-lockup__subtitle span');
                        const locationEl = card.querySelector('.job-card-container__metadata-wrapper li span');
                        if (!titleEl) return;
                        const href = titleEl.getAttribute('href');
                        if (!href) return;
                        const footerText = card.innerText || '';
                        if (footerText.includes('Applied') || footerText.includes('Application submitted')) return;
                        results.push({
                            title:    titleEl.innerText.trim().split('\\n')[0].trim(),
                            company:  companyEl  ? companyEl.innerText.trim()  : '',
                            location: locationEl ? locationEl.innerText.trim() : '',
                            url:      'https://www.linkedin.com' + href.split('?')[0],
                        });
                    });
                    return results;
                }
            """)

            for card in cards:
                results.append(RawJob(
                    title=card["title"],
                    company=card["company"],
                    location=card["location"],
                    url=card["url"],
                    source=self.name,
                ))

            page_num += 1
            new_on_page = sum(1 for c in cards if c["url"] not in known_urls) if known_urls is not None else len(cards)
            new_total += new_on_page
            print(f"  Page {page_num}: {len(cards)} cards ({new_on_page} new, total: {len(results)})")

            if known_urls is not None and new_on_page == 0 and len(cards) > 0:
                print("  All cards on this page are known, stopping early.")
                break

            cap_count = new_total if known_urls is not None else len(results)
            if max_jobs and cap_count >= max_jobs:
                break
            if page_num >= _MAX_PAGES_PER_SEARCH:
                print(f"  Reached page limit ({_MAX_PAGES_PER_SEARCH}), stopping this search.")
                break

            next_btn = self._page.query_selector("button[aria-label='View next page']")
            if not next_btn:
                break

            next_btn.click()
            self._wait(min_sec=5, max_sec=15)

        return results[:max_jobs] if max_jobs else results
