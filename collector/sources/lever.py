from datetime import datetime, timezone

from collector.base import RawJob
from collector.sources.ats import ATSBoardSource

_URL = "https://api.lever.co/v0/postings/{slug}"


class LeverSource(ATSBoardSource):
    provider = "lever"

    def _fetch_board(self, board: dict) -> list[RawJob]:
        try:
            response = self._client.get(_URL.format(slug=board["slug"]), params={"mode": "json"})
            response.raise_for_status()
            rows = response.json()
        except Exception:
            return []
        jobs = []
        for row in rows if isinstance(rows, list) else []:
            created = row.get("createdAt")
            url = row.get("hostedUrl") or row.get("applyUrl") or ""
            if not created or not url:
                continue
            categories = row.get("categories") or {}
            locations = categories.get("allLocations") or []
            location = ", ".join(locations) if locations else categories.get("location") or ""
            lists = "\n\n".join(
                f"{item.get('text', '')}\n{item.get('content', '')}" for item in row.get("lists") or []
            )
            description = "\n\n".join(filter(None, [
                row.get("descriptionPlain"), lists, row.get("additionalPlain"),
            ]))
            source_data = {"remote": True, "remote_regions": [location]} if "remote" in location.lower() else None
            jobs.append(RawJob(
                title=row.get("text") or "",
                company=board["name"],
                location=location,
                url=url,
                source=self.name,
                source_id=f"{board['slug']}:{row.get('id', '')}",
                description=description or None,
                posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).isoformat(),
                source_structured_data=source_data,
            ))
        return jobs
