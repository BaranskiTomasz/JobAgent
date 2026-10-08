from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from collector.base import RawJob
from collector.sources.ats import ATSBoardSource
from collector.utils import strip_html

_URL = "https://api.lever.co/v0/postings/{slug}"
_PAGE_SIZE = 100


def _timestamp(value) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            number = float(value)
            divisor = 1000 if abs(number) > 10_000_000_000 else 1
            return datetime.fromtimestamp(number / divisor, timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class LeverSource(ATSBoardSource):
    provider = "lever"

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
        parsed = urlsplit((url or "").strip())
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
        normalized = {self._url_key(url) for url in (known_urls or set())}
        known = set(known_urls or set())
        if normalized:
            known.update(
                job.url
                for job in self._fetch_jobs()
                if self._url_key(job.url) in normalized
            )
        results = super().search(
            title,
            location,
            days_back=days_back,
            max_results=max_results,
            known_urls=known,
        )
        diagnostics = dict(getattr(self, "last_search_diagnostics", {}))
        failed = len(self._board_errors)
        diagnostics.update(
            boards_configured=len(self._boards),
            boards_failed=failed,
            source_status=(
                "error"
                if failed == len(self._boards) and self._boards
                else "partial"
                if failed
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
        rows = []
        skip = 0
        try:
            while True:
                response = self._client.get(
                    _URL.format(slug=board["slug"]),
                    params={"mode": "json", "limit": _PAGE_SIZE, "skip": skip},
                )
                response.raise_for_status()
                payload = response.json()
                page = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
                if not isinstance(page, list):
                    raise ValueError("response postings field is not a list")
                if not page:
                    break
                rows.extend(item for item in page if isinstance(item, dict))
                if len(page) < _PAGE_SIZE:
                    break
                page_ids = [item.get("id") or item.get("hostedUrl") for item in page]
                old_ids = {item.get("id") or item.get("hostedUrl") for item in rows[:-len(page)]}
                if page_ids and not any(item not in old_ids for item in page_ids):
                    break
                skip += len(page)
        except (httpx.HTTPError, RuntimeError, TypeError, ValueError) as exc:
            self._board_errors[board["slug"]] = str(exc)
        jobs = []
        seen = set()
        for row in rows if isinstance(rows, list) else []:
            created = _timestamp(row.get("createdAt") or row.get("updatedAt"))
            url = row.get("hostedUrl") or row.get("applyUrl") or ""
            if not created or not url:
                continue
            identity = self._url_key(url)
            if identity in seen:
                continue
            seen.add(identity)
            categories = row.get("categories") if isinstance(row.get("categories"), dict) else {}
            locations = categories.get("allLocations") or categories.get("location") or []
            if isinstance(locations, str):
                locations = [locations]
            locations = [
                item.get("name") if isinstance(item, dict) else str(item)
                for item in locations
                if item
            ]
            locations = [item for item in locations if item]
            location = ", ".join(dict.fromkeys(locations))
            lists = "\n\n".join(
                f"{item.get('text', '')}\n{strip_html(item.get('content', '') or '') or ''}"
                for item in row.get("lists") or []
                if isinstance(item, dict)
            )
            description = "\n\n".join(
                str(part) for part in (row.get("descriptionPlain"), lists, row.get("additionalPlain")) if part
            )
            workplace = str(row.get("workplaceType") or "").casefold()
            location_is_remote = "remote" in location.casefold()
            source_data = (
                {
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": locations,
                }
                if workplace == "remote" or location_is_remote
                else None
            )
            jobs.append(RawJob(
                title=row.get("text") or "",
                company=board["name"],
                location=location,
                url=url,
                source=self.name,
                source_id=(
                    f"{board['slug']}:{row.get('id')}"
                    if row.get("id") is not None
                    else self._url_key(url)
                ),
                description=description or None,
                posted_at=created.isoformat(),
                source_structured_data=source_data,
            ))
        return jobs
