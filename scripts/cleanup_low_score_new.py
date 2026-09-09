"""One-off/rerunnable cleanup: convert existing 'new' jobs already scored at/below
the auto-reject threshold (config.SCORING) to status='auto_rejected'.

evaluator/runner.py now auto-rejects low scores as they're produced, but that only
applies going forward, this catches jobs that were scored before that logic existed.
Safe to rerun any time; it's a no-op once nothing 'new' is left at/below the threshold.
"""
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
logger = logging.getLogger(__name__)

from db.repositories import job_repository
from config import SCORING

threshold = SCORING["auto_reject_at_or_below"]
jobs = job_repository.search(status="new")
to_reject = [j for j in jobs if j.get("score") is not None and j["score"] <= threshold]

if not to_reject:
    print(f"No 'new' jobs at or below score {threshold} to clean up.")
    sys.exit(0)

print(f"Converting {len(to_reject)} 'new' job(s) with score <= {threshold} to auto_rejected...")
for job in to_reject:
    job_repository.update_score_and_status(job["id"], job["score"], job["score_reason"], "auto_rejected")
    print(f"  [{job['score']}] {job['title']} @ {job['company']}")

print(f"\nDone. {len(to_reject)} job(s) auto-rejected.")
