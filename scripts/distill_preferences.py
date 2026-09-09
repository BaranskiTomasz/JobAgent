"""Run preference distillation once. Used as a pipeline step before scoring."""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

from preference_agent.runner import run

result = run()

if result.get("ok"):
    reason = result.get("reason", "")
    if reason == "no_new_data":
        print("Preferences up to date, no new feedback since last distillation.")
    else:
        n = len(result.get("signals", []))
        print(f"Distillation complete. {n} preference signal(s) saved.")
else:
    reason = result.get("reason", "unknown")
    if reason == "no_data":
        print("No feedback yet, skipping distillation.")
    else:
        print(f"Distillation skipped: {reason}")
