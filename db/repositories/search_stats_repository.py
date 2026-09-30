import api_client


def record(session_id: int, source: str, search_query: str, location: str, cards_found: int, new_found: int,
           upstream_found: int | None = None, query_matched: int | None = None,
           date_matched: int | None = None, geo_matched: int | None = None) -> None:
    api_client.post("/api/search-stats", json={
        "session_id": session_id, "source": source, "search_query": search_query,
        "location": location, "cards_found": cards_found, "new_found": new_found,
        "upstream_found": upstream_found, "query_matched": query_matched,
        "date_matched": date_matched, "geo_matched": geo_matched,
    })


def get_query_summary(source: str) -> list[dict]:
    """Per search_query totals across all recorded runs, zero_result_searches counts
    individual (query, location) calls that found no cards at all, not full runs."""
    return api_client.get("/api/search-stats/summary", params={"source": source}).json()


def get_zero_yield_queries(source: str, min_searches: int) -> list[str]:
    """search_query values searched at least min_searches times for this source
    where every single search found zero *new* jobs."""
    return api_client.get("/api/search-stats/zero-yield", params={"source": source, "min_searches": min_searches}).json()
