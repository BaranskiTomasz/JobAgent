"""Re-score all 'new' jobs using current preferences (overwrites existing scores safely)."""
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

from db.repositories import job_repository
from evaluator.runner import run as evaluate

jobs = job_repository.get_new_with_descriptions()
if not jobs:
    print("No 'new' jobs with descriptions to re-score.")
    sys.exit(0)

print(f"Will re-score {len(jobs)} job(s) with current preferences...")
result = evaluate(force_rescore=True, jobs=jobs)
print(f"\nDone. Scored {result.get('jobs_scored', 0)} job(s).")
