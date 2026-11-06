"""
Full pipeline: nudge the server-side catalog collection, collect new jobs from
LinkedIn, then score them with Claude.

Usage:
    python scripts/run_all.py
    python scripts/run_all.py --days 3 --max-jobs 30
    python scripts/run_all.py --log-file data/logs/run.log
"""
# -*- coding: utf-8 -*-
import argparse
import logging
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from api_client import catalog_collection_running, nudge_catalog_collection
from collector.runner import run as collect
from db.repositories import job_repository, session_repository, usage_repository
from evaluator.runner import run as evaluate
from extractor.runner import run_extraction

logger = logging.getLogger(__name__)

# After nudging JobAgentWeb's catalog collection, poll its status every
# CATALOG_NUDGE_POLL_INTERVAL_SECONDS and give up after CATALOG_NUDGE_WAIT_BUDGET_SECONDS
# total elapsed (the first poll's grace sleep counts toward that budget).
CATALOG_NUDGE_POLL_INTERVAL_SECONDS = 7
CATALOG_NUDGE_WAIT_BUDGET_SECONDS = 300  # 5 minutes


def _configure_logging(log_path: str | None) -> list[logging.Handler]:
    """Set up root logger with stdout + log-file handlers. Returns handlers for cleanup."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")

    handlers: list[logging.Handler] = []

    stream_h = logging.StreamHandler(sys.stdout)
    stream_h.setFormatter(fmt)
    root.addHandler(stream_h)
    handlers.append(stream_h)

    current_log = os.path.join(ROOT, "data", "logs", "current_run.log")
    os.makedirs(os.path.dirname(current_log), exist_ok=True)
    file_h = logging.FileHandler(current_log, mode="w", encoding="utf-8")
    file_h.setFormatter(fmt)
    root.addHandler(file_h)
    handlers.append(file_h)

    if log_path:
        user_h = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        user_h.setFormatter(fmt)
        root.addHandler(user_h)
        handlers.append(user_h)

    return handlers


def _run_script(name: str) -> int:
    # These scripts have no importable entrypoint, so run as a subprocess,
    # matching the dashboard pipeline's own approach for these stages.
    import subprocess
    script_path = os.path.join(ROOT, "scripts", name)
    return subprocess.run([sys.executable, script_path], check=False).returncode


def _nudge_catalog_collection() -> None:
    """Nudge JobAgentWeb's server-side catalog collection to run now and wait,
    for at most CATALOG_NUDGE_WAIT_BUDGET_SECONDS total, until it finishes, so
    this run's later stages see fresh catalog jobs. Non-fatal by design: any
    request error or the wait budget expiring just logs and lets the pipeline
    continue."""
    try:
        nudge_catalog_collection()
    except Exception as e:
        logger.warning(f"Catalog collection nudge request failed (non-fatal): {e}")
        return

    deadline = time.monotonic() + CATALOG_NUDGE_WAIT_BUDGET_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "Catalog collection did not finish within the 5-minute wait budget; "
                "proceeding anyway (non-fatal)."
            )
            return
        # Sleep before every status check, the first one included: right after
        # the nudge returns the server has only just scheduled the run and its
        # running flag may not be set yet, so a False reading at t=0 would be
        # mistaken for "already done". The sleep is clamped to the remaining
        # budget, so the grace sleep counts toward - and never pushes past - the
        # 5-minute total.
        time.sleep(min(CATALOG_NUDGE_POLL_INTERVAL_SECONDS, remaining))
        if time.monotonic() >= deadline:
            logger.warning(
                "Catalog collection did not finish within the 5-minute wait budget; "
                "proceeding anyway (non-fatal)."
            )
            return
        try:
            running = catalog_collection_running()
        except Exception as e:
            logger.warning(f"Catalog collection status check failed (non-fatal): {e}")
            return
        if not running:
            logger.info("Catalog collection nudge finished waiting.")
            return


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full JobAgent pipeline: collect then evaluate")
    parser.add_argument("--days",           type=int,  default=None, help="Days back to search (default: since the last successful collection, like the dashboard)")
    parser.add_argument("--max-jobs",       type=int,  default=None, help="Max new jobs to collect (default: unlimited)")
    parser.add_argument("--max-jobs-per-source", type=int, default=None, help="Max new jobs from each source")
    parser.add_argument("--titles",         nargs="*", default=None, help="Override job titles (scoring only)")
    parser.add_argument("--search-queries", nargs="*", default=None, help="Override LinkedIn search queries")
    parser.add_argument("--locations",      nargs="*", default=None, help="Override search locations")
    parser.add_argument("--log-file",       default=None,            help="Append output to this file (e.g. data/logs/run.log)")
    parser.add_argument("--skip-ranking",   action="store_true",     help="Skip embeddings and ranking")
    args = parser.parse_args()

    handlers = _configure_logging(args.log_file)
    # This CLI entry point never went through web/routes/runner.py's
    # started_at -> record_run_summary envelope, so every run_all.py run's
    # cost (collector, distill, extractor, evaluator, prune, ranking, all of
    # it) was silently missing from cost_summaries entirely.
    started_at = usage_repository.now_iso()

    try:
        logger.info("=" * 60)
        logger.info(f"JobAgent run started, {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        logger.info("=" * 60)

        # Catalog collection runs server-side in JobAgentWeb on its own daily
        # schedule; nudge it now and wait (bounded) for it, so the extraction and
        # evaluation stages below see fresh catalog jobs.
        logger.info("\n=== CATALOG NUDGE ===")
        _nudge_catalog_collection()

        days_back = args.days
        if days_back is None:
            days_back = session_repository.days_since_last_collection()
            logger.info(f"No --days given, using {days_back} day(s) since the last successful collection.")

        logger.info("\n=== COLLECTOR ===")
        try:
            c_result = collect(
                days_back=days_back,
                max_jobs=args.max_jobs,
                titles=args.titles,
                search_queries_override=args.search_queries,
                locations=args.locations,
                max_jobs_per_source=args.max_jobs_per_source,
            )
        except Exception as e:
            logger.error(f"Collector failed: {e}")
            logger.info("Skipping evaluator.")
            return 1

        logger.info(f"\nCollector result: found={c_result['jobs_found']}  new={c_result['jobs_new']}")

        # Matches the dashboard pipeline's stage order (web/routes/runner.py):
        # COLLECTOR -> DISTILL -> EXTRACTOR -> EVALUATOR -> PRUNE QUERIES -> RANKING.
        logger.info("\n=== DISTILL PREFERENCES ===")
        if _run_script("distill_preferences.py") != 0:
            logger.warning("Preference distillation failed (non-fatal)")

        # Must precede EVALUATOR, dealbreakers.py reads structured_data, and a job
        # never re-enters the unscored pool once scored.
        logger.info("\n=== EXTRACTOR ===")
        try:
            new_jobs = job_repository.get_new_needing_extraction()
            extracted = run_extraction(new_jobs)
            logger.info(f"Extractor result: structured_data updated for {extracted} job(s)")
        except Exception as e:
            logger.warning(f"Extractor failed (non-fatal): {e}")

        logger.info("\n=== EVALUATOR ===")
        try:
            evaluation_jobs = job_repository.get_unscored()
            e_result = evaluate(jobs=evaluation_jobs) if evaluation_jobs else {"jobs_scored": 0}
        except Exception as e:
            logger.error(f"Evaluator failed: {e}")
            return 1

        logger.info(f"\nEvaluator result: scored={e_result.get('jobs_scored', 0)}")

        logger.info("\n=== PRUNE QUERIES ===")
        if _run_script("prune_search_queries.py") != 0:
            logger.warning("Search query pruning failed (non-fatal)")

        if args.skip_ranking:
            logger.info("\n=== EMBEDDINGS + RANKING ===\nSkipped by --skip-ranking")
        else:
            logger.info("\n=== EMBEDDINGS + RANKING ===")
            if _run_script("rank_jobs.py") != 0:
                logger.warning("Ranking failed (non-fatal)")

        logger.info("\n" + "=" * 60)
        logger.info("Run complete.")
        return 0

    finally:
        usage_repository.record_run_summary("run_all", started_at)
        for h in handlers:
            h.close()
            logging.getLogger().removeHandler(h)


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    sys.exit(main())
