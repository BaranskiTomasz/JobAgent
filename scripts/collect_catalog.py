import argparse
import logging
import sys
from datetime import date

from collector.runner import run


CATALOG_QUERIES = (
    "PHP Developer",
    "Python Developer",
    "Node.js Developer",
    "React Developer",
    "Angular Developer",
    "QA Engineer",
)
CATALOG_COUNTRIES = ("Poland", "Bulgaria")


def queries_for_slot(slot: int, per_run: int = 2) -> list[str]:
    start = (slot * per_run) % len(CATALOG_QUERIES)
    return [CATALOG_QUERIES[(start + offset) % len(CATALOG_QUERIES)] for offset in range(per_run)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect the shared public remote-job catalog")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-jobs", type=int, default=60)
    parser.add_argument("--max-jobs-per-source", type=int, default=20)
    parser.add_argument("--queries-per-run", type=int, default=2, choices=range(1, 7))
    parser.add_argument("--slot", type=int, default=date.today().toordinal())
    parser.add_argument("--sources", nargs="*", default=None)
    args = parser.parse_args()

    queries = queries_for_slot(args.slot, args.queries_per_run)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    logging.info("Catalog queries: %s", ", ".join(queries))
    result = run(
        days_back=args.days,
        max_jobs=args.max_jobs,
        locations=list(CATALOG_COUNTRIES),
        search_queries_override=queries,
        source_ids=args.sources,
        max_jobs_per_source=args.max_jobs_per_source,
        profile_routing=False,
    )
    logging.info("Catalog collection complete: found=%s new=%s", result["jobs_found"], result["jobs_new"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
