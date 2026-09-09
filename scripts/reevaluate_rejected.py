"""Reset auto-rejected jobs and re-run evaluator + keyword filter with updated criteria."""
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

import api_client
from collector.filters import apply_keyword_filter
from evaluator.runner import run as evaluate

n = api_client.post("/api/jobs/reset-auto-rejected").json()["reset"]

if n == 0:
    print("No auto-rejected jobs with descriptions to reset.")
    sys.exit(0)

print(f"Reset {n} auto-rejected job(s) → new")

print("\n=== KEYWORD FILTER ===")
result = apply_keyword_filter()
if result["checked"]:
    print(f"Checked {result['checked']} job(s), auto-rejected {result['auto_rejected']}")
else:
    print("No criteria configured, all jobs passed through")

print("\n=== EVALUATOR ===")
eval_result = evaluate()
print(f"Scored {eval_result.get('jobs_scored', 0)} job(s)")
