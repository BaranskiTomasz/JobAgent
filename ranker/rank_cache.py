# Skip re-running listwise+debate ranking when the candidate pool hasn't
# changed since the last run: Opus + debate calls are the expensive, repeated
# cost in the daily ranking pipeline.
from ranker.debate import DEBATE_UNAVAILABLE_FLAG
from ranker.listwise import FALLBACK_RANK_REASON


def _is_degraded(job: dict) -> bool:
    # True if this job's stored ranking is a failure fallback, not a genuine
    # listwise/debate result: reusing it would perpetuate whatever outage
    # produced it for as long as the candidate pool stays the same.
    return job.get("rank_reason") == FALLBACK_RANK_REASON or job.get("debate_flag") == DEBATE_UNAVAILABLE_FLAG


def reuse_if_unchanged(
    listwise_pool: list[dict], previously_fetched_jobs: list[dict], fingerprint: str | None = None
) -> list[dict] | None:
    # Returns the previous run's ranked jobs if the exact same job IDs were
    # already ranked last run AND that result was a genuine listwise/debate
    # outcome, else None (caller should re-run listwise_rank + debate_rank).
    if not listwise_pool:
        return []
    previous_ranked = [j for j in previously_fetched_jobs if j.get("listwise_rank") is not None]
    current_ids = {j["id"] for j in listwise_pool}
    previous_ids = {j["id"] for j in previous_ranked}
    if current_ids != previous_ids:
        return None
    if any(_is_degraded(j) for j in previous_ranked):
        return None
    if fingerprint is not None and any(j.get("ranking_fingerprint") != fingerprint for j in previous_ranked):
        return None
    return sorted(listwise_pool, key=lambda j: j["listwise_rank"])
