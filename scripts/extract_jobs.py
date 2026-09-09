"""Backfill structured data extraction for all jobs that don't have it yet."""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.repositories import job_repository
from extractor.runner import run_extraction

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

pending = job_repository.get_missing_structured_data()
logger.info(f"Jobs to extract: {len(pending)}")

if not pending:
    logger.info("Nothing to do.")
    sys.exit(0)

updated = run_extraction(pending)
logger.info(f"Done. Extracted: {updated}")
