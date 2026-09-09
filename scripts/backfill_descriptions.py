"""Retry fetching descriptions for jobs that are missing them (any source, stealth-safe
for LinkedIn, direct for everything else)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.repositories import job_repository
from collector.runner import _fetch_descriptions_stealthily, _fetch_descriptions_directly

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")

missing = job_repository.get_missing_descriptions()
total = len(missing)

if not total:
    print("No jobs with missing descriptions.")
    print("PROGRESS:0/0")
    sys.exit(0)

print(f"Found {total} job(s) without descriptions. Fetching...")
print(f"PROGRESS:0/{total}")

by_source: dict[str, list[tuple[str, str]]] = {}
for job in missing:
    by_source.setdefault(job["source"], []).append((job["id"], job["url"]))

done = ok_total = failed_total = 0
for source_id, jobs in by_source.items():
    if source_id == "linkedin":
        ok, failed = _fetch_descriptions_stealthily(jobs)
    else:
        ok, failed = _fetch_descriptions_directly(source_id, jobs)
    ok_total += ok
    failed_total += failed
    done += len(jobs)
    print(f"PROGRESS:{done}/{total}")

print(f"\nDone. Fetched: {ok_total}  Still missing: {failed_total}")
