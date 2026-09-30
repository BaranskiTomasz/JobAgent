from html import unescape

from collector.base import RawJob
from collector.sources.ats import ATSBoardSource, _parse_iso
from collector.utils import strip_html

_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


class GreenhouseSource(ATSBoardSource):
    provider = "greenhouse"

    def _fetch_board(self, board: dict) -> list[RawJob]:
        try:
            response = self._client.get(_URL.format(slug=board["slug"]), params={"content": "true"})
            response.raise_for_status()
            rows = response.json().get("jobs", [])
        except Exception:
            return []
        jobs = []
        for row in rows:
            published = _parse_iso(row.get("first_published") or row.get("updated_at"))
            url = row.get("absolute_url") or ""
            if not published or not url:
                continue
            jobs.append(RawJob(
                title=row.get("title") or "",
                company=row.get("company_name") or board["name"],
                location=(row.get("location") or {}).get("name") or "",
                url=url,
                source=self.name,
                source_id=f"{board['slug']}:{row.get('id', '')}",
                description=strip_html(unescape(row.get("content") or "")),
                posted_at=published.isoformat(),
            ))
        return jobs
