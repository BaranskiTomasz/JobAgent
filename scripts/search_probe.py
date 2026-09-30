import argparse
import json

from collector.query_planner import source_queries
from collector.runner import _locations_for_source
from collector.sources import make
from db.repositories import candidate_preferences_repository, criteria_repository


def probe(sources: list[str], queries: list[str], locations: list[str], days: int, limit: int,
          work_country: str | None = None) -> dict:
    report = {"sources": {}}
    for source_id in sources:
        source_report = []
        planned_queries = source_queries(source_id, queries)
        planned_locations = _locations_for_source(source_id, locations, work_country)
        try:
            source = make(source_id, days_back=days)
            with source:
                source.login()
                for query in planned_queries:
                    for location in planned_locations:
                        jobs = source.search(query, location, max_results=limit, known_urls=set())
                        source_report.append({
                            "query": query,
                            "location": location,
                            "diagnostics": getattr(source, "last_search_diagnostics", {}),
                            "jobs": [{
                                "title": job.title,
                                "company": job.company,
                                "location": job.location,
                                "url": job.url,
                            } for job in jobs],
                        })
        except Exception as exc:
            source_report.append({"error": str(exc)})
        report["sources"][source_id] = source_report
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect source search quality without writing jobs")
    parser.add_argument("--sources", nargs="+", required=True)
    parser.add_argument("--queries", nargs="*")
    parser.add_argument("--locations", nargs="*")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    criteria = criteria_repository.get_active_dict()
    preferences = candidate_preferences_repository.get_active() or {}
    queries = args.queries or criteria["search_queries"] or criteria["titles"]
    locations = args.locations or criteria["locations"]
    if not queries or not locations:
        raise SystemExit("No queries or locations configured")
    work_country = (preferences.get("work_country") or "").strip() or None
    print(json.dumps(
        probe(args.sources, queries, locations, args.days, args.limit, work_country),
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
