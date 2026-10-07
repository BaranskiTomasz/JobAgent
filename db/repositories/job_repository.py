"""Every function here calls JobAgentWeb's /api/jobs/* over HTTP (api_client.py)
instead of touching a local database, JobAgentWeb is the sole data store now,
shared across every user's collector/evaluator/ranker."""
import api_client


def insert(
    title: str,
    company: str,
    location: str,
    url: str,
    source: str,
    source_id: str | None = None,
    description: str | None = None,
    search_query: str | None = None,
    posted_at: str | None = None,
    source_structured_data: dict | None = None,
) -> str | None:
    resp = api_client.post("/api/jobs", json={
        "title": title, "company": company, "location": location, "url": url,
        "source": source, "source_id": source_id, "description": description,
        "search_query": search_query, "posted_at": posted_at,
        "source_structured_data": source_structured_data,
    })
    return resp.json()["job_id"]


def get_all_urls() -> set[str]:
    """Return all known job URLs (system-wide, not just this user's) as a set
    for fast early-stop deduplication, see JobAgentWeb's jobs_repo.get_all_urls."""
    return set(api_client.get("/api/jobs/urls").json()["urls"])


def get_missing_descriptions() -> list[dict]:
    return api_client.get("/api/jobs/missing-descriptions").json()


def get_missing_structured_data() -> list[dict]:
    return api_client.get("/api/jobs/missing-structured-data").json()


def get_missing_facts(schema_version: int, limit: int = 200) -> list[dict]:
    return api_client.get(
        "/api/jobs/missing-facts", params={"schema_version": schema_version, "limit": limit},
    ).json()


def update_facts(
    job_id: str, schema_version: int, model: str, content_hash: str,
    facts: dict, provenance: dict,
) -> str:
    response = api_client.put(f"/api/jobs/{job_id}/facts", json={
        "schema_version": schema_version,
        "model": model,
        "content_hash": content_hash,
        "facts": facts,
        "provenance": provenance,
    })
    return response.json().get("job_id", job_id)


def update_description(job_id: str, description: str) -> str:
    response = api_client.patch(f"/api/jobs/{job_id}/description", json={"description": description})
    return response.json().get("id", job_id)


def update_score(job_id: str, score: float, reason: str, breakdown: dict | None = None, fingerprint: str | None = None) -> None:
    api_client.patch(f"/api/jobs/{job_id}/score", json={"score": score, "reason": reason, "breakdown": breakdown, "fingerprint": fingerprint})


def update_status(job_id: str, status: str, rejection_reason: str | None = None) -> None:
    api_client.patch(f"/api/jobs/{job_id}/status", json={"status": status, "rejection_reason": rejection_reason})


def update_score_and_status(job_id: str, score: float | None, reason: str, status: str, breakdown: dict | None = None, fingerprint: str | None = None) -> None:
    api_client.patch(f"/api/jobs/{job_id}/score-and-status", json={
        "score": score, "reason": reason, "status": status, "breakdown": breakdown, "fingerprint": fingerprint,
    })


def get_new() -> list[dict]:
    return api_client.get("/api/jobs/new").json()


def get_unscored() -> list[dict]:
    return api_client.get("/api/jobs/unscored").json()


def get_new_with_descriptions() -> list[dict]:
    return api_client.get("/api/jobs/new-with-descriptions").json()


def get_dealbreaker_rejected_with_descriptions() -> list[dict]:
    return api_client.get("/api/jobs/dealbreaker-rejected-with-descriptions").json()


def get_by_status(status: str) -> list[dict]:
    return api_client.get("/api/jobs", params={"status": status}).json()


def get_examples(limit_positive: int = 25, limit_negative: int = 25) -> tuple[list[dict], list[dict]]:
    data = api_client.get("/api/jobs/examples", params={
        "limit_positive": limit_positive, "limit_negative": limit_negative,
    }).json()
    return data["positive"], data["negative"]


def get_all_feedback(limit_applied: int | None = None, limit_rejected: int | None = None) -> tuple[list[dict], list[dict]]:
    params = {}
    if limit_applied is not None:
        params["limit_applied"] = limit_applied
    if limit_rejected is not None:
        params["limit_rejected"] = limit_rejected
    data = api_client.get("/api/jobs/feedback", params=params).json()
    return data["applied"], data["rejected"]


def get_feedback_since(since_timestamp: str) -> tuple[list[dict], list[dict]]:
    data = api_client.get("/api/jobs/feedback", params={"since": since_timestamp}).json()
    return data["applied"], data["rejected"]


def search(
    status: str | None = None,
    min_score: float | None = None,
    query: str | None = None,
    source: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict]:
    params = {}
    if status is not None:
        params["status"] = status
    if min_score is not None:
        params["min_score"] = min_score
    if query is not None:
        params["query"] = query
    if source is not None:
        params["source"] = source
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    return api_client.get("/api/jobs", params=params).json()


def count_by_filter(statuses: list[str], date_from: str | None = None, date_to: str | None = None) -> int:
    if not statuses:
        return 0
    params = [("status", s) for s in statuses]
    if date_from:
        params.append(("date_from", date_from))
    if date_to:
        params.append(("date_to", date_to))
    return api_client.get("/api/jobs/count", params=params).json()["count"]


def delete_by_filter(statuses: list[str], date_from: str | None = None, date_to: str | None = None) -> int:
    """'Delete' removes these jobs from *your* view only, see JobAgentWeb's
    jobs_repo.delete_by_filter for why the shared posting itself is untouched."""
    if not statuses:
        return 0
    params = [("status", s) for s in statuses]
    if date_from:
        params.append(("date_from", date_from))
    if date_to:
        params.append(("date_to", date_to))
    return api_client.delete("/api/jobs", params=params).json()["deleted"]


def update_structured_data(job_id: str, data: dict) -> None:
    api_client.patch(f"/api/jobs/{job_id}/structured-data", json={"data": data})


def update_ranking_scores(
    job_id: str,
    embedding_score: float | None,
    rerank_score: float | None,
    listwise_rank: int | None,
    rank_reason: str | None = None,
    debate_flag: str | None = None,
    debate_note: str | None = None,
    fingerprint: str | None = None,
) -> None:
    api_client.patch(f"/api/jobs/{job_id}/ranking", json={
        "embedding_score": embedding_score, "rerank_score": rerank_score, "listwise_rank": listwise_rank,
        "rank_reason": rank_reason, "debate_flag": debate_flag, "debate_note": debate_note, "fingerprint": fingerprint,
    })


def update_ranking_scores_batch(items: list[dict]) -> int:
    """One request for the whole batch instead of one PATCH per job. Each item:
    {job_id, embedding_score, rerank_score, listwise_rank, rank_reason?, debate_flag?, debate_note?}."""
    if not items:
        return 0
    return api_client.patch("/api/jobs/ranking", json={"items": items}).json()["updated"]


def get_jobs_for_ranking(limit: int = 2000) -> list[dict]:
    """All 'new' jobs with descriptions, the whole active pool is re-ranked together every
    run (not just newly-arrived jobs) so listwise_rank stays comparable across the full list."""
    return api_client.get("/api/jobs/for-ranking", params={"limit": limit}).json()


def get_applied_job_ids() -> list[str]:
    return api_client.get("/api/jobs/applied-ids").json()["ids"]


def get_rejected_job_ids() -> list[str]:
    return api_client.get("/api/jobs/rejected-ids").json()["ids"]


def count_decisions() -> int:
    return api_client.get("/api/jobs/decisions-count").json()["count"]


def get_query_outcome_stats(source: str) -> list[dict]:
    """Per search_query outcome totals for one source, feeds scripts/prune_search_queries.py."""
    return api_client.get("/api/jobs/query-outcome-stats", params={"source": source}).json()


def update_would_apply(job_id: str, would_apply: bool, reason: str) -> None:
    api_client.patch(f"/api/jobs/{job_id}/would-apply", json={"would_apply": would_apply, "reason": reason})


def update_would_apply_batch(items: list[dict]) -> int:
    """Each item: {job_id, would_apply, reason}."""
    if not items:
        return 0
    return api_client.patch("/api/jobs/would-apply", json={"items": items}).json()["updated"]


def get_would_apply_stats() -> dict:
    return api_client.get("/api/jobs/would-apply-stats").json()


def get_stats() -> dict:
    return api_client.get("/api/jobs/stats").json()
