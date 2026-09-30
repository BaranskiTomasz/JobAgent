from collector.base import RawJob
from collector.sources.ats import ATSBoardSource, _parse_iso
from collector.utils import strip_html

_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


class AshbySource(ATSBoardSource):
    provider = "ashby"

    def _fetch_board(self, board: dict) -> list[RawJob]:
        try:
            response = self._client.get(_URL.format(slug=board["slug"]), params={"includeCompensation": "true"})
            response.raise_for_status()
            rows = response.json().get("jobs", [])
        except Exception:
            return []
        jobs = []
        for row in rows:
            if row.get("isListed") is False:
                continue
            published = _parse_iso(row.get("publishedAt"))
            url = row.get("jobUrl") or row.get("applyUrl") or ""
            if not published or not url:
                continue
            locations = [row.get("location") or ""]
            locations.extend(
                item.get("location") or item.get("name") or ""
                for item in row.get("secondaryLocations") or []
            )
            location = ", ".join(dict.fromkeys(item for item in locations if item))
            source_data = {}
            if row.get("isRemote") is True or (row.get("workplaceType") or "").lower() == "remote":
                source_data.update({"remote": True, "remote_regions": [location] if location else []})
            jobs.append(RawJob(
                title=row.get("title") or "",
                company=board["name"],
                location=location,
                url=url,
                source=self.name,
                source_id=f"{board['slug']}:{row.get('id', '')}",
                description=strip_html(row.get("descriptionHtml") or ""),
                posted_at=published.isoformat(),
                source_structured_data=source_data or None,
            ))
        return jobs
