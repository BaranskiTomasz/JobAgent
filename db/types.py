from typing import TypedDict


class JobRow(TypedDict):
    id: str
    title: str
    company: str
    location: str
    url: str
    description: str | None
    source: str
    source_id: str | None
    status: str
    score: float | None
    score_reason: str | None
    score_breakdown: str | None
    rejection_reason: str | None
    created_at: str
    updated_at: str


class JobStats(TypedDict):
    total: int
    new: int
    reviewed: int
    applied: int
    rejected: int
    auto_rejected: int
    avg_score: float | None
    avg_score_new: float | None
    last_run: str | None


class ScoreResult(TypedDict):
    score: float | None
    score_reason: str
    breakdown: dict | None


class ScoringBatch(TypedDict):
    early_result: dict | None
    jobs: list[dict]
    system_prompt: str | None
    # Keyed by job["id"] as-is, whatever type the API returns it as (not str(id) —
    # that normalization is only done for the separate manifest/in-flight files).
    fingerprints: dict
    dealbreaker_stats: dict | None
