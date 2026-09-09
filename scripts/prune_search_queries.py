"""Evaluate LinkedIn search queries against config.QUERY_PRUNING's thresholds and
auto-exclude any that have proven reject-heavy with a vanishing positive-yield rate,
or that consistently find nothing new. Reversible, see
db.repositories.excluded_search_queries_repository.reinstate()."""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector.query_pruning import prune_queries

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

newly_excluded = prune_queries()
if not newly_excluded:
    logger.info("No search queries met the exclusion thresholds.")
else:
    for item in newly_excluded:
        logger.info(f"  [excluded] {item['search_query']!r}, {item['reason']}")
    logger.info(f"Done. {len(newly_excluded)} quer{'y' if len(newly_excluded) == 1 else 'ies'} excluded.")
