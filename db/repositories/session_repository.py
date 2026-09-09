import math
from datetime import datetime

import api_client

DEFAULT_DAYS_NO_PRIOR_RUN = 7


def start() -> int:
    return api_client.post("/api/sessions").json()["id"]


def finish(session_id: int, jobs_found: int, jobs_scored: int, status: str = "done") -> None:
    api_client.patch(f"/api/sessions/{session_id}/finish", json={
        "jobs_found": jobs_found, "jobs_scored": jobs_scored, "status": status,
    })


def cancel_active() -> None:
    api_client.post("/api/sessions/cancel-active")


def has_active_run() -> bool:
    return api_client.get("/api/sessions/has-active").json()["active"]


def get_last_finished_at() -> str | None:
    return api_client.get("/api/sessions/last-finished").json()["finished_at"]


def mark_collected(session_id: int) -> None:
    api_client.post(f"/api/sessions/{session_id}/mark-collected")


def get_last_collected_at() -> str | None:
    return api_client.get("/api/sessions/last-collected").json()["collected_at"]


def get_latest() -> dict | None:
    return api_client.get("/api/sessions/latest").json()["session"]


def days_since_last_collection() -> int:
    # Rounded up since collector sources filter by whole days; slight overlap
    # is harmless (the collector dedupes by URL), under-covering would miss
    # postings. Reads last-collected, not last-finished, since a non-collector
    # run (ranking, rescoring) would otherwise narrow this window.
    last_collected = get_last_collected_at()
    if not last_collected:
        return DEFAULT_DAYS_NO_PRIOR_RUN
    try:
        last_dt = datetime.fromisoformat(last_collected)
    except ValueError:
        return DEFAULT_DAYS_NO_PRIOR_RUN
    hours_elapsed = (datetime.utcnow() - last_dt).total_seconds() / 3600
    return max(1, math.ceil(hours_elapsed / 24))
