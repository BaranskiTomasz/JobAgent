"""Score the job backlog offline through the Anthropic Message Batches API.

This path is 50% cheaper and may take up to 24 hours to complete. It complements
the interactive, synchronous evaluator/runner.py run() path and does not replace it.

Usage:
    python scripts/batch_score_backlog.py submit
    python scripts/batch_score_backlog.py collect <batch_id>
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, SCORING
from db.repositories import job_repository
from db.repositories.usage_repository import log_anthropic
from evaluator.runner import atomic_write_json, gather_scoring_batch, load_in_flight, save_in_flight
from evaluator.scorer import build_user_message, parse_score_response, SCORE_TOOL

logger = logging.getLogger(__name__)

_MANIFEST_DIR = Path(__file__).resolve().parent.parent / "data" / "batch_scoring"


def _manifest_path(batch_id: str) -> Path:
    return _MANIFEST_DIR / f"{batch_id}.json"


def submit() -> int:
    scoring_batch = gather_scoring_batch()
    if scoring_batch["early_result"] is not None:
        logger.info(f"Nothing to submit: {scoring_batch['early_result']}")
        return 0

    requests = [
        {
            "custom_id": str(job["id"]),
            "params": {
                "model": CLAUDE_MODEL,
                "max_tokens": 700,
                "system": [{
                    "type": "text",
                    "text": scoring_batch["system_prompt"],
                    "cache_control": {"type": "ephemeral"},
                }],
                "messages": [{"role": "user", "content": build_user_message(job)}],
                "tools": [SCORE_TOOL],
                "tool_choice": {"type": "tool", "name": "submit_score"},
            },
        }
        for job in scoring_batch["jobs"]
    ]

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    batch = client.messages.batches.create(requests=requests)

    manifest = {
        str(job["id"]): scoring_batch["fingerprints"][job["id"]]
        for job in scoring_batch["jobs"]
    }
    atomic_write_json(_manifest_path(batch.id), manifest)

    submitted_at = datetime.now(timezone.utc).isoformat()
    in_flight = load_in_flight()
    in_flight.update({
        str(job["id"]): {"batch_id": batch.id, "submitted_at": submitted_at}
        for job in scoring_batch["jobs"]
    })
    save_in_flight(in_flight)

    print(f"Submitted batch {batch.id} with {len(requests)} job(s).")
    print(f"Run 'python scripts/batch_score_backlog.py collect {batch.id}' later; processing may take up to 24 hours.")
    return 0


def collect(batch_id: str) -> int:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        print(f"Batch {batch_id} status: {batch.processing_status}. Run collect again later.")
        return 0

    manifest_path = _manifest_path(batch_id)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Batch manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    in_flight = load_in_flight()

    jobs_scored = 0
    jobs_auto_rejected = 0
    failed = 0

    for item in client.messages.batches.results(batch_id):
        job_id = item.custom_id
        fingerprint = manifest.get(job_id)
        if fingerprint is None:
            logger.debug(
                f"Job {job_id} already applied in a previous collect() run for this "
                "batch (or not part of it); skipping."
            )
            continue

        if item.result.type == "succeeded":
            message = item.result.message
            # Message Batches API usage is billed at a 50% discount.
            if message and message.content:
                log_anthropic(message, "scorer_batch", CLAUDE_MODEL, discount=0.5)
            score_result = parse_score_response(message)
            if score_result["score"] is None:
                logger.warning(
                    f"Scoring failed, will retry next run: {score_result['score_reason']}"
                )
            elif score_result["score"] <= SCORING["auto_reject_at_or_below"]:
                job_repository.update_score_and_status(
                    job_id,
                    score_result["score"],
                    score_result["score_reason"],
                    "auto_rejected",
                    score_result.get("breakdown"),
                    fingerprint,
                )
                jobs_auto_rejected += 1
            else:
                job_repository.update_score(
                    job_id,
                    score_result["score"],
                    score_result["score_reason"],
                    score_result.get("breakdown"),
                    fingerprint,
                )
            if score_result["score"] is not None:
                jobs_scored += 1
        elif item.result.type in ("errored", "expired", "canceled"):
            failed += 1
            logger.warning(
                f"Batch scoring {item.result.type} for job {job_id}; will retry next run."
            )
        else:
            # Unknown result.type (e.g. a future SDK addition) — leave the job in
            # both tracking files rather than silently discarding its state; it'll
            # show up in the "still pending" count below for a human to look at.
            logger.warning(
                f"Job {job_id}: unrecognized batch result type {item.result.type!r}, "
                "leaving it in the manifest for investigation."
            )
            continue

        # Checkpoint after every item, not periodically: a realistic backlog here is
        # tens of jobs, not thousands, so the extra I/O is cheap and shrinks the
        # re-billed/re-applied window on a crash to at most the single item in
        # flight when it happens, instead of the whole batch.
        manifest.pop(job_id, None)
        in_flight.pop(job_id, None)
        atomic_write_json(manifest_path, manifest)
        save_in_flight(in_flight)

    print(
        f"Done. Scored: {jobs_scored}, auto-rejected: {jobs_auto_rejected}, "
        f"errored/expired/canceled: {failed}."
    )
    if manifest:
        print(
            f"{len(manifest)} job(s) still pending in the manifest — results() may not "
            "have returned everything; run collect again."
        )
    else:
        manifest_path.unlink()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Submit or collect offline job scoring through Anthropic Message Batches"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("submit", help="Submit the current scoring backlog")
    collect_parser = subparsers.add_parser("collect", help="Collect a completed batch")
    collect_parser.add_argument("batch_id", help="Anthropic Message Batch ID")
    args = parser.parse_args()

    if args.command == "submit":
        return submit()
    return collect(args.batch_id)


if __name__ == "__main__":
    sys.exit(main())
