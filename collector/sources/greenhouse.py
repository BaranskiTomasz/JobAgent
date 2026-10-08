from html import unescape
from urllib.parse import urlsplit, urlunsplit

import httpx

from collector.base import RawJob
from collector.sources.ats import ATSBoardSource, _parse_iso
from collector.utils import strip_html

_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


class GreenhouseSource(ATSBoardSource):
    provider = "greenhouse"

    def __init__(self, days_back: int = 7, **kwargs):
        super().__init__(days_back=days_back, **kwargs)
        self._board_errors: dict[str, str] = {}

    def __enter__(self):
        super().__enter__()
        self._board_errors = {}
        return self

    def __exit__(self, *args):
        self._board_errors = {}
        return super().__exit__(*args)

    @staticmethod
    def _url_key(url: str) -> str:
        parsed = urlsplit(url.strip())
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")
        )

    def search(
        self,
        title,
        location,
        days_back=None,
        max_results=None,
        known_urls=None,
    ):
        normalized_known_urls = {self._url_key(url) for url in (known_urls or set())}
        known_urls = set(known_urls or set())
        if normalized_known_urls:
            known_urls.update(
                job.url
                for job in self._fetch_jobs()
                if self._url_key(job.url) in normalized_known_urls
            )
        results = super().search(
            title,
            location,
            days_back=days_back,
            max_results=max_results,
            known_urls=known_urls,
        )
        diagnostics = dict(getattr(self, "last_search_diagnostics", {}))
        failed_boards = len(self._board_errors)
        diagnostics.update(
            boards_configured=len(self._boards),
            boards_failed=failed_boards,
            source_status=(
                "error"
                if failed_boards == len(self._boards) and self._boards
                else "partial"
                if failed_boards
                else "empty"
                if not diagnostics.get("upstream_found")
                else "ok"
            ),
        )
        if self._board_errors:
            diagnostics["source_error"] = "; ".join(
                f"{slug}: {error}" for slug, error in sorted(self._board_errors.items())
            )
        self.set_search_diagnostics(**diagnostics)
        return results

    def _fetch_board(self, board: dict) -> list[RawJob]:
        try:
            response = self._client.get(_URL.format(slug=board["slug"]), params={"content": "true"})
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("jobs", []) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                raise ValueError("response jobs field is not a list")
        except (httpx.HTTPError, RuntimeError, TypeError, ValueError) as exc:
            self._board_errors[board["slug"]] = str(exc)
            return []
        jobs = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            published = _parse_iso(row.get("first_published") or row.get("updated_at"))
            url = row.get("absolute_url") or ""
            if not published or not url:
                continue
            job_id = row.get("id")
            source_id = f"{board['slug']}:{job_id}" if job_id is not None else self._url_key(url)
            location_data = row.get("location") or {}
            location = location_data.get("name") or "" if isinstance(location_data, dict) else ""
            description = strip_html(unescape(row.get("content") or "")) or ""
            source_data = {}
            if "remote" in location.casefold():
                source_data = {
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": [location],
                }
            jobs.append(RawJob(
                title=row.get("title") or "",
                company=row.get("company_name") or board["name"],
                location=location,
                url=url,
                source=self.name,
                source_id=source_id,
                description=description,
                posted_at=published.isoformat(),
                source_structured_data=source_data or None,
            ))
        return jobs
