import logging

from config import QUERY_PRUNING
from db.repositories import job_repository, search_stats_repository, excluded_search_queries_repository

logger = logging.getLogger(__name__)


def _reject_rate_reason(stats: dict) -> str | None:
    # A query is only judged once it has enough terminally-decided jobs, and
    # stays protected while its applied/reviewed share stays above
    # max_success_rate, see config.QUERY_PRUNING for why.
    terminal_total = stats["terminal_total"]
    if terminal_total < QUERY_PRUNING["min_terminal_sample"]:
        return None
    success_total = stats["applied_total"] + stats["reviewed_total"]
    success_rate = success_total / terminal_total
    if success_rate > QUERY_PRUNING["max_success_rate"]:
        return None
    reject_rate = stats["reject_total"] / terminal_total
    if reject_rate < QUERY_PRUNING["reject_rate_threshold"]:
        return None
    return f"reject rate {reject_rate:.0%} over {terminal_total} jobs, {success_rate:.0%} applied/reviewed"


def suggest_queries_for_review(source: str | None = None) -> list[dict]:
    # Queries with zero applied jobs and a reject rate at or above
    # suggestion_reject_rate_threshold: a lower, human-reviewed bar than
    # prune_queries' automatic exclusion, for the case where a query regularly
    # produces reviewable-but-never-applied jobs and so stays auto-protected
    # indefinitely. Never auto-excludes; the caller decides what to act on.
    source = source or QUERY_PRUNING["source"]
    already_excluded = excluded_search_queries_repository.get_excluded(source)
    suggestions: list[dict] = []

    for stats in job_repository.get_query_outcome_stats(source):
        query = stats["search_query"]
        if not query or query in already_excluded:
            continue
        terminal_total = stats["terminal_total"]
        if terminal_total < QUERY_PRUNING["min_terminal_sample"]:
            continue
        if stats["applied_total"] > 0:
            continue
        reject_rate = stats["reject_total"] / terminal_total
        if reject_rate < QUERY_PRUNING["suggestion_reject_rate_threshold"]:
            continue
        suggestions.append({
            "search_query": query,
            "reject_rate": reject_rate,
            "terminal_total": terminal_total,
            "reviewed_total": stats["reviewed_total"],
            "reason": (
                f"{reject_rate:.0%} reject rate over {terminal_total} jobs, 0 applied "
                f"({stats['reviewed_total']} reviewed, never converted)"
            ),
        })

    suggestions.sort(key=lambda s: s["reject_rate"], reverse=True)
    return suggestions


def prune_queries(source: str | None = None) -> list[dict]:
    # Idempotent: exclude() upserts, so re-running just refreshes the stored reason.
    source = source or QUERY_PRUNING["source"]
    excluded_now: list[dict] = []

    for stats in job_repository.get_query_outcome_stats(source):
        reason = _reject_rate_reason(stats)
        if reason:
            excluded_search_queries_repository.exclude(source, stats["search_query"], reason)
            excluded_now.append({"search_query": stats["search_query"], "reason": reason})

    already = {e["search_query"] for e in excluded_now}
    min_searches = QUERY_PRUNING["min_searches_for_zero_yield"]
    for query in search_stats_repository.get_zero_yield_queries(source, min_searches):
        if query in already:
            continue
        reason = f"returned zero eligible matches across {min_searches}+ searches"
        excluded_search_queries_repository.exclude(source, query, reason)
        excluded_now.append({"search_query": query, "reason": reason})

    return excluded_now
