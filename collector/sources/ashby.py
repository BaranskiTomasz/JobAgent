from urllib.parse import urlsplit, urlunsplit

import httpx

from collector.base import RawJob
from collector.sources.ats import ATSBoardSource, _parse_iso
from collector.utils import strip_html

_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


class AshbySource(ATSBoardSource):
    provider = "ashby"

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
        try:
            response = self._client.get(
                _URL.format(slug=board["slug"]),
                params={"includeCompensation": "true"},
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("jobs", payload.get("data", [])) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                raise ValueError("response jobs field is not a list")
        except (httpx.HTTPError, RuntimeError, TypeError, ValueError) as exc:
            self._board_errors[board["slug"]] = str(exc)
            return []
        jobs = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("isListed") is False:
                continue
            published = _parse_iso(row.get("publishedAt"))
            url = row.get("jobUrl") or row.get("applyUrl") or ""
            if not published or not url:
                continue
            identity = self._url_key(url)
            if identity in seen:
                continue
            seen.add(identity)
            primary_location = str(row.get("location") or "").strip()
            locations = [primary_location]
            locations.extend(
                item.get("location") or item.get("name") or "" if isinstance(item, dict) else str(item)
                for item in row.get("secondaryLocations") or []
            )
            locations = [str(item) for item in locations if item]
            locations = list(dict.fromkeys(locations))
            location = ", ".join(locations)
            description = (
                strip_html(row.get("descriptionHtml") or "")
                or row.get("descriptionPlain")
                or None
            )
            source_data = {}
            workplace = str(row.get("workplaceType") or "").casefold()
            native_remote = row.get("isRemote") is True or workplace == "remote"
            address = row.get("address") if isinstance(row.get("address"), dict) else {}
            postal = address.get("postalAddress") if isinstance(address.get("postalAddress"), dict) else {}
            address_country = str(postal.get("addressCountry") or "").strip()
            remote_regions = locations
            if native_remote and primary_location.casefold() in {"remote", "remote only"}:
                remote_regions = [address_country] if address_country else locations[1:]
                if remote_regions:
                    location = f"Remote - {', '.join(remote_regions)}"
            elif native_remote and locations:
                location = f"Remote - {', '.join(locations)}"
            if native_remote:
                source_data.update({
                    "remote": True,
                    "remote_available": True,
                    "remote_regions": remote_regions,
                })
            compensation = row.get("compensation") if isinstance(row.get("compensation"), dict) else {}
            salary = next(
                (
                    item
                    for item in compensation.get("summaryComponents") or []
                    if isinstance(item, dict)
                    and str(item.get("compensationType") or "").casefold() == "salary"
                ),
                None,
            )
            if salary is None and isinstance(compensation.get("salaryRange"), dict):
                salary = compensation["salaryRange"]
            if salary:
                source_data["_salary_disclosed"] = True
                for field, key in (
                    ("minValue", "salary_min"),
                    ("maxValue", "salary_max"),
                    ("currencyCode", "salary_currency"),
                ):
                    if salary.get(field) is not None:
                        source_data[key] = salary[field]
                if "salary_currency" not in source_data and salary.get("currency"):
                    source_data["salary_currency"] = salary["currency"]
                interval = str(salary.get("interval") or "").casefold()
                if "year" in interval:
                    source_data["salary_period"] = "yearly"
                elif "month" in interval:
                    source_data["salary_period"] = "monthly"
                elif "hour" in interval:
                    source_data["salary_period"] = "hourly"
            jobs.append(RawJob(
                title=row.get("title") or row.get("name") or "",
                company=board["name"],
                location=location,
                url=url,
                source=self.name,
                source_id=(
                    f"{board['slug']}:{row.get('id')}"
                    if row.get("id") is not None
                    else self._url_key(url)
                ),
                description=description,
                posted_at=published.isoformat(),
                source_structured_data=source_data or None,
            ))
        return jobs
