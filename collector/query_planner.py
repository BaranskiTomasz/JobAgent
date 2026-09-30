from dataclasses import dataclass
from datetime import datetime

from collector.query_matcher import primary_query_token


_TAG_SOURCES = {"jobicy"}


@dataclass(frozen=True)
class SourceQuery:
    original: str
    outbound: str


def source_queries(source: str, queries: list[str]) -> list[SourceQuery]:
    planned: list[SourceQuery] = []
    seen: set[tuple[str, str]] = set()
    for query in queries:
        original = query.strip()
        outbound = primary_query_token(original) if source in _TAG_SOURCES else original
        key = (original.casefold(), outbound.casefold())
        if original and outbound and key not in seen:
            planned.append(SourceQuery(original=original, outbound=outbound))
            seen.add(key)
    return planned


def order_queries(queries: list[SourceQuery], summary: list[dict], run_id: int) -> list[SourceQuery]:
    if len(queries) < 2:
        return queries
    stats = {row["search_query"].casefold(): row for row in summary}
    unseen = [query for query in queries if query.original.casefold() not in stats]
    seen = [query for query in queries if query.original.casefold() in stats]
    if unseen:
        offset = run_id % len(unseen)
        unseen = unseen[offset:] + unseen[:offset]

    def key(query: SourceQuery):
        row = stats[query.original.casefold()]
        searched = row.get("last_searched_at")
        if isinstance(searched, str):
            try:
                searched = datetime.fromisoformat(searched.replace("Z", "+00:00"))
            except ValueError:
                searched = None
        searches = row.get("total_searches") or 0
        new_found = row.get("total_new_found") or 0
        searched_key = searched.timestamp() if searched else float("-inf")
        return (searched_key, -(new_found / searches if searches else 0))

    return unseen + sorted(seen, key=key)
