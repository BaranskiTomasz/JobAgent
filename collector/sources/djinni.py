import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from collector.base import JobSource, RawJob
from collector.sources.rss import fetch_jobposting_ld_json, fetch_rss_items, item_text, parse_pub_date
from collector.utils import strip_html


_FEED_URL = "https://djinni.co/jobs/rss/"
_PAGE_FETCH_DELAY_SECONDS = 1.0
_PRIMARY_KEYWORD_BY_QUERY = {
    "qa": "QA",
    "qa engineer": "QA",
    "quality assurance": "QA",
    "python": "Python",
    "python developer": "Python",
    "java": "Java",
    "java developer": "Java",
    "javascript": "JavaScript",
    "frontend developer": "JavaScript",
    "front end developer": "JavaScript",
    ".net developer": "ASP.NET",
    "devops": "DevOps",
    "business analyst": "Business Analyst",
    "project manager": "Project Manager",
    "product manager": "Product Manager",
    "data engineer": "Data Engineer",
    "data scientist": "Data Science",
}
_JOB_ID_RE = re.compile(r"/jobs/(\d+)(?:-|/)")


def _country_name(payload: dict) -> str:
    requirement = payload.get("applicantLocationRequirements") or {}
    address = requirement.get("address") or {} if isinstance(requirement, dict) else {}
    code = address.get("addressCountry") if isinstance(address, dict) else None
    return "Ukraine" if str(code).upper() in {"UA", "UKR", "UKRAINE"} else str(code or "")


class DjinniSource(JobSource):
    requires_stealth_pauses = False

    def __init__(self, days_back: int = 7, **_):
        self._days_back = days_back
        self.last_search_diagnostics: dict[str, object] = {}

    @property
    def name(self) -> str:
        return "djinni"

    def search(
        self,
        title: str,
        location: str,
        days_back: int | None = None,
        max_results: int | None = None,
        known_urls: set[str] | None = None,
    ) -> list[RawJob]:
        keyword = _PRIMARY_KEYWORD_BY_QUERY.get(title.strip().casefold())
        if not keyword:
            self.set_search_diagnostics(source_status="empty", source_returned=0)
            return []
        feed_url = f"{_FEED_URL}?{urlencode({'primary_keyword': keyword})}"
        try:
            items = fetch_rss_items(feed_url)
        except RuntimeError as exc:
            self.set_search_diagnostics(source_status="error", source_error=str(exc))
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back or self._days_back)
        known = known_urls or set()
        candidates: list[tuple[str, datetime]] = []
        date_matched = 0
        for item in items:
            published = parse_pub_date(item)
            url = item_text(item, "link")
            if published is None or published < cutoff or not url:
                continue
            date_matched += 1
            if url not in known:
                candidates.append((url, published))

        jobs: list[RawJob] = []
        page_failures = 0
        for index, (url, feed_published) in enumerate(candidates):
            if index:
                time.sleep(_PAGE_FETCH_DELAY_SECONDS)
            payload = fetch_jobposting_ld_json(url)
            if not payload:
                page_failures += 1
                continue
            organization = payload.get("hiringOrganization") or {}
            company = organization.get("name") if isinstance(organization, dict) else None
            job_title = payload.get("title")
            if not company or not job_title:
                page_failures += 1
                continue
            is_remote = payload.get("jobLocationType") == "TELECOMMUTE"
            region = _country_name(payload)
            structured: dict[str, object] = {}
            if is_remote:
                structured.update({"remote": True, "remote_available": True})
                if region:
                    structured["remote_regions"] = [region]
            job_id = _JOB_ID_RE.search(urlsplit(url).path)
            jobs.append(RawJob(
                title=str(job_title),
                company=str(company),
                location="Remote" if is_remote else region,
                url=url,
                source=self.name,
                source_id=job_id.group(1) if job_id else None,
                description=strip_html(str(payload.get("description") or "")),
                posted_at=str(payload.get("datePosted") or feed_published.isoformat()),
                source_structured_data=structured or None,
            ))
            if max_results is not None and len(jobs) >= max_results:
                break

        status = "partial" if page_failures and jobs else "empty" if not jobs else "ok"
        self.set_search_diagnostics(
            upstream_found=len(items),
            date_matched=date_matched,
            source_returned=len(jobs),
            known_url_filtered=max(date_matched - len(candidates), 0),
            source_status=status,
            source_error=f"Could not parse {page_failures} job page(s)" if page_failures else None,
        )
        return jobs
