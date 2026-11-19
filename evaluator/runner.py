import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from config import SCORING
from db.repositories import job_repository, criteria_repository, preference_repository
from db.types import ScoringBatch
from evaluation.harness import divergence_cases
from evaluator.dealbreakers import apply_dealbreaker_filter
from evaluator.scorer import score_job, build_system_prompt
from evaluator.profile import load_active_profile, load_questionnaire_preferences
from evaluator.fingerprints import score_fingerprint

# Cap on how many past ranking mistakes get fed back into the scoring prompt, bounds
# prompt size as more divergence cases accumulate over time.
_CALIBRATION_LIMIT = 10

# Jobs submitted to an outstanding Message Batch (scripts/batch_score_backlog.py)
# are excluded from scoring here so a sync run() can't silently re-score (and
# re-bill) the same job while its batch is still in flight. State lives in a
# local file, not a DB column, deliberately: this is a single-user CLI tool, and
# adding a column would need a JobAgentWeb migration for a concern that's purely
# "has this client already sent this job to Anthropic" — not something another
# account or service needs to see.
_IN_FLIGHT_PATH = Path(__file__).resolve().parent.parent / "data" / "batch_scoring" / "_in_flight.json"

# Generous margin above Anthropic's documented 24h max batch processing time —
# an entry older than this almost certainly means the batch_id was lost (e.g. the
# terminal output wasn't saved) rather than still genuinely processing, so treat
# it as abandoned and let the job be scored again instead of hiding it forever.
_IN_FLIGHT_TTL = timedelta(hours=48)


def atomic_write_json(path: Path, data: dict) -> None:
    """Writes `data` as JSON to `path` via a same-directory temp file + os.replace,
    so a crash mid-write never leaves a truncated/corrupt file at `path` — shared by
    every local JSON state file this pipeline keeps (in-flight tracking here, and
    the per-batch manifest in scripts/batch_score_backlog.py)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary_path.replace(path)


def load_in_flight() -> dict[str, dict]:
    """Returns {job_id: {"batch_id": ..., "submitted_at": <iso8601>}} for jobs
    currently submitted to an outstanding batch. Prunes (and persists the pruning
    of) entries older than _IN_FLIGHT_TTL as a side effect, so an abandoned batch
    (its id never collected) doesn't hide a job from scoring forever."""
    if not _IN_FLIGHT_PATH.exists():
        return {}
    try:
        raw = json.loads(_IN_FLIGHT_PATH.read_text(encoding="utf-8"))
        cutoff = datetime.now(timezone.utc) - _IN_FLIGHT_TTL
        fresh, expired = {}, []
        for job_id, entry in raw.items():
            submitted_at = datetime.fromisoformat(entry["submitted_at"])
            if submitted_at < cutoff:
                expired.append(job_id)
            else:
                fresh[job_id] = entry
    except Exception:
        # Fail open: a job wrongly treated as "not in flight" gets scored again
        # (wasteful, not harmful) — the alternative, propagating this, would stop
        # both run() and submit() cold until a human manually fixes the file.
        logger.warning(f"Malformed {_IN_FLIGHT_PATH}, ignoring it.", exc_info=True)
        return {}

    if expired:
        logger.warning(
            f"Dropping {len(expired)} abandoned in-flight batch entry/entries "
            f"(older than {_IN_FLIGHT_TTL}, batch_id likely lost): {expired}"
        )
        save_in_flight(fresh)

    return fresh


def save_in_flight(data: dict[str, dict]) -> None:
    atomic_write_json(_IN_FLIGHT_PATH, data)


def _early(early_result: dict, dealbreaker_stats: dict | None = None) -> ScoringBatch:
    return {
        "early_result": early_result,
        "jobs": [],
        "system_prompt": None,
        "fingerprints": {},
        "dealbreaker_stats": dealbreaker_stats,
    }


def gather_scoring_batch(
    force_rescore: bool = False, jobs: list[dict] | None = None,
) -> ScoringBatch:
    """Shared setup for both the interactive scorer (run()) and the batch/offline
    scoring script (scripts/batch_score_backlog.py). Returns a dict with keys:
      - "early_result": dict | None — when NOT None, this is exactly the dict run()
        would return at this point today (e.g. {"jobs_scored": 0} or
        {"jobs_scored": 0, "jobs_auto_rejected": N}); the caller should just stop and
        return/report that, ignoring the other keys.
      - "jobs": list[dict] — unscored_jobs (empty when early_result is not None)
      - "system_prompt": str | None
      - "fingerprints": dict[str, str] — job id -> score_fingerprint
      - "dealbreaker_stats": dict | None
    """
    # `jobs` lets a caller that already fetched the same list pass it straight
    # through instead of re-fetching, only meaningful with force_rescore=True.
    #
    # rescore_all controls whether every surviving job gets sent to the LLM
    # regardless of an existing score, vs. the normal path (score IS NULL only).
    if jobs is not None:
        full_pool = jobs
        rescore_all = True
    elif force_rescore:
        full_pool = job_repository.get_new_with_descriptions() + job_repository.get_dealbreaker_rejected_with_descriptions()
        if full_pool:
            logger.info(f"Force-rescore mode: {len(full_pool)} job(s) will be re-scored.")
        rescore_all = True
    else:
        full_pool = job_repository.get_new_with_descriptions()
        rescore_all = False

    if not full_pool:
        logger.info("No jobs to evaluate.")
        return _early({"jobs_scored": 0})

    previously_dealbreaker_rejected = {
        job["id"] for job in full_pool
        if job.get("status") == "auto_rejected" and (job.get("score_reason") or "").startswith("Dealbreaker:")
    }
    surviving, dealbreaker_stats = apply_dealbreaker_filter(full_pool)
    for job in surviving:
        if job["id"] in previously_dealbreaker_rejected:
            job_repository.update_score_and_status(job["id"], None, "", "new", None, None)
            job["status"] = "new"
            job["score"] = None
            job["score_fingerprint"] = None
    if dealbreaker_stats["auto_rejected"]:
        logger.info(f"Dealbreaker filter: auto-rejected {dealbreaker_stats['auto_rejected']}/{dealbreaker_stats['checked']} job(s) before scoring")
    if not surviving:
        logger.info("No jobs left to evaluate after dealbreaker filter.")
        return _early(
            {"jobs_scored": 0, "jobs_auto_rejected": dealbreaker_stats["auto_rejected"]},
            dealbreaker_stats,
        )

    try:
        candidate_profile = load_active_profile()
    except ValueError as e:
        logger.error(str(e))
        return _early({"jobs_scored": 0}, dealbreaker_stats)

    criteria = criteria_repository.get_active_dict()
    questionnaire = load_questionnaire_preferences()

    latest_preference = preference_repository.get_latest()
    learned_preferences = latest_preference["signals"] if latest_preference else []

    example_limit = 8 if learned_preferences else 25
    positive_examples, negative_examples = job_repository.get_examples(
        limit_positive=example_limit, limit_negative=example_limit
    )

    calibration_cases = divergence_cases()[:_CALIBRATION_LIMIT]
    if calibration_cases:
        logger.info(f"Calibration: feeding {len(calibration_cases)} past ranking divergence(s) into scoring")

    shared_system_prompt = build_system_prompt(
        criteria, positive_examples, negative_examples, candidate_profile, learned_preferences,
        divergence_cases=calibration_cases, questionnaire=questionnaire,
    )

    fingerprints = {job["id"]: score_fingerprint(job, shared_system_prompt) for job in surviving}
    unscored_jobs = surviving if rescore_all else [
        job for job in surviving
        if job.get("score") is None or job.get("score_fingerprint") != fingerprints[job["id"]]
    ]
    if not rescore_all:
        # Only guard the default path (rescore_all=False). force_rescore=True
        # (scripts/rescore_new.py) or an explicit jobs= list (scripts/run_all.py) is
        # the caller explicitly saying "score these now regardless" — silently
        # dropping some of them because an unrelated batch happens to be in flight
        # would contradict that explicit intent. scripts/reevaluate_rejected.py calls
        # evaluate() with no arguments, so it stays on the default, guarded path.
        in_flight_job_ids = set(load_in_flight())
        held_back_count = sum(1 for job in unscored_jobs if str(job["id"]) in in_flight_job_ids)
        if held_back_count:
            logger.info(
                f"{held_back_count} job(s) held back from scoring: already submitted to an "
                "outstanding batch (scripts/batch_score_backlog.py collect <batch_id> to apply it)."
            )
        unscored_jobs = [
            job for job in unscored_jobs if str(job["id"]) not in in_flight_job_ids
        ]
    if not unscored_jobs:
        logger.info("No jobs left to score after dealbreaker filter.")
        return _early(
            {"jobs_scored": 0, "jobs_auto_rejected": dealbreaker_stats["auto_rejected"]},
            dealbreaker_stats,
        )

    logger.info(f"Evaluating {len(unscored_jobs)} job(s)...")
    if learned_preferences:
        logger.info(f"Preference profile active ({latest_preference['applied_count']} applied, {latest_preference['rejected_count']} rejected)")
        logger.info(f"Grounding examples: {len(positive_examples)} applied, {len(negative_examples)} rejected")
    else:
        logger.info(f"No preference profile, few-shot: {len(positive_examples)} applied, {len(negative_examples)} rejected")
    logger.info("=" * 50)

    return {
        "early_result": None,
        "jobs": unscored_jobs,
        "system_prompt": shared_system_prompt,
        "fingerprints": fingerprints,
        "dealbreaker_stats": dealbreaker_stats,
    }


def run(force_rescore: bool = False, jobs: list[dict] | None = None) -> dict:
    batch = gather_scoring_batch(force_rescore, jobs)
    if batch["early_result"] is not None:
        return batch["early_result"]

    unscored_jobs = batch["jobs"]
    shared_system_prompt = batch["system_prompt"]
    fingerprints = batch["fingerprints"]
    dealbreaker_stats = batch["dealbreaker_stats"]

    auto_reject_threshold = SCORING["auto_reject_at_or_below"]
    jobs_scored = 0
    jobs_auto_rejected = 0

    for job in unscored_jobs:
        logger.info(f"\n[{jobs_scored + 1}/{len(unscored_jobs)}] {job['title']} @ {job['company']}")

        result = score_job(job=job, system_prompt=shared_system_prompt)
        if result["score"] is None:
            logger.warning(f"  Scoring failed, will retry next run: {result['score_reason']}")
            continue

        if result["score"] <= auto_reject_threshold:
            job_repository.update_score_and_status(
                job["id"], result["score"], result["score_reason"], "auto_rejected", result.get("breakdown"), fingerprints[job["id"]]
            )
            jobs_auto_rejected += 1
            logger.info(f"  Score: {result['score']}/10, auto-rejected, {result['score_reason']}")
        else:
            job_repository.update_score(job["id"], result["score"], result["score_reason"], result.get("breakdown"), fingerprints[job["id"]])
            logger.info(f"  Score: {result['score']}/10, {result['score_reason']}")

        jobs_scored += 1

    total_auto_rejected = jobs_auto_rejected + dealbreaker_stats["auto_rejected"]
    logger.info("\n" + "=" * 50)
    logger.info(f"Done. Scored: {jobs_scored} (auto-rejected: {total_auto_rejected}, of which {dealbreaker_stats['auto_rejected']} by dealbreaker filter)")

    return {"jobs_scored": jobs_scored, "jobs_auto_rejected": total_auto_rejected}


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    run()
