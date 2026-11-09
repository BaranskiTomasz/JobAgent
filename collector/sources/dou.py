import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from collector.base import JobSource, RawJob
from collector.sources.rss import fetch_rss_items, item_text, parse_pub_date
from collector.utils import strip_html


_FEED_URL = "https://jobs.dou.ua/vacancies/feeds/"
_CATEGORY_BY_QUERY = {
    "qa": "QA",
    "qa engineer": "QA",
    "quality assurance": "QA",
    "python": "Python",
    "python developer": "Python",
    "java": "Java",
    "java developer": "Java",
    "javascript": "Front End",
    "frontend developer": "Front End",
    "front end developer": "Front End",
    ".net developer": ".NET",
    "devops": "DevOps",
    "business analyst": "Analyst",
    "project manager": "Project Manager",
    "product manager": "Product Manager",
    "data engineer": "Data Engineer",
    "data scientist": "Data Science",
}
_REMOTE_MARKERS = frozenset({"віддалено", "remote"})
_SALARY_RE = re.compile(
    r"[$€₴]|^\s*(?:до|від)(?:\s|\xa0)|^\s*\d[\d\s\xa0–—-]*$",
    re.IGNORECASE,
)
_VACANCY_ID_RE = re.compile(r"/vacancies/(\d+)/")
_COMPANY_SLUG_RE = re.compile(r"/companies/([^/]+)/")


def _feed_url(query: str) -> str:
    category = _CATEGORY_BY_QUERY.get(query.strip().casefold())
    params = {"category": category} if category else {"search": query.strip()}
    return f"{_FEED_URL}?{urlencode(params)}&remote"


def _company_from_url(url: str) -> str:
    match = _COMPANY_SLUG_RE.search(urlsplit(url).path)
    return match.group(1).replace("-", " ").title() if match else ""


def _clean_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode([
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() != "utm_source"
    ])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _parse_title(title: str, url: str) -> tuple[str, str, str, bool]:
    role, separator, tail = title.rpartition(" в ")
    if not separator:
        role, tail = title, ""
    segments = [part.strip() for part in tail.split(",")]
    company = segments[0] if segments and segments[0] else _company_from_url(url)
    markers = [part for part in segments[1:] if part and not _SALARY_RE.search(part)]
    is_remote = any(marker.casefold() in _REMOTE_MARKERS for marker in markers)
    if is_remote:
        location = "Remote"
    else:
        location_parts = ["Abroad" if marker.casefold() == "за кордоном" else marker for marker in markers]
        location = ", ".join(location_parts)
    return role.strip(), company, location, is_remote


class DouSource(JobSource):
    requires_stealth_pauses = False

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self.last_search_diagnostics: dict[str, object] = {}

    @property
    def name(self) -> str:
        return "dou"

    def search(
        self,
        title: str,
        location: str,
        days_back: int | None = None,
        max_results: int | None = None,
        known_urls: set[str] | None = None,
    ) -> list[RawJob]:
        try:
            items = fetch_rss_items(_feed_url(title))
        except RuntimeError as exc:
            self.set_search_diagnostics(source_status="error", source_error=str(exc))
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back or self._days_back)
        known = known_urls or set()
        jobs: list[RawJob] = []
        date_matched = 0
        for item in items:
            try:
                published = parse_pub_date(item)
                if published is None or published < cutoff:
                    continue
                date_matched += 1
                raw_url = item_text(item, "link")
                url = _clean_url(raw_url)
                raw_title = item_text(item, "title")
                if not raw_url or not raw_title or url in known:
                    continue
                role, company, job_location, is_remote = _parse_title(raw_title, raw_url)
                if not role or not company:
                    continue
                vacancy_id = _VACANCY_ID_RE.search(urlsplit(raw_url).path)
                structured = (
                    {"remote": True, "remote_available": True, "remote_regions": ["Ukraine"]}
                    if is_remote else None
                )
                jobs.append(RawJob(
                    title=role,
                    company=company,
                    location=job_location,
                    url=url,
                    source=self.name,
                    source_id=vacancy_id.group(1) if vacancy_id else None,
                    description=strip_html(item_text(item, "description")),
                    posted_at=published.isoformat(),
                    source_structured_data=structured,
                ))
            except (AttributeError, TypeError, ValueError):
                continue
            if max_results is not None and len(jobs) >= max_results:
                break

        self.set_search_diagnostics(
            upstream_found=len(items),
            date_matched=date_matched,
            source_returned=len(jobs),
            known_url_filtered=max(date_matched - len(jobs), 0),
            source_status="ok" if jobs else "empty",
        )
        return jobs
