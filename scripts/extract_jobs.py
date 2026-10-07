"""Backfill versioned job facts."""
import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.repositories import job_repository
from extractor.runner import FACT_SCHEMA_VERSION, run_extraction

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=int, default=200)
parser.add_argument("--max-age-days", type=int, default=14)
args = parser.parse_args()

pending = job_repository.get_missing_facts(
    FACT_SCHEMA_VERSION, args.limit, args.max_age_days,
)
logger.info(f"Jobs to extract: {len(pending)}")

if not pending:
    logger.info("Nothing to do.")
    sys.exit(0)

updated = run_extraction(pending)
logger.info(f"Done. Extracted: {updated}")
