import sys
import logging

logger = logging.getLogger(__name__)

from config import SCORING
from db.repositories import job_repository, criteria_repository, preference_repository
from evaluation.harness import divergence_cases
from evaluator.dealbreakers import apply_dealbreaker_filter
from evaluator.scorer import score_job, build_system_prompt
from evaluator.profile import load_active_profile, load_questionnaire_preferences
from evaluator.fingerprints import score_fingerprint

# Cap on how many past ranking mistakes get fed back into the scoring prompt, bounds
# prompt size as more divergence cases accumulate over time.
_CALIBRATION_LIMIT = 10


def run(force_rescore: bool = False, jobs: list[dict] | None = None) -> dict:
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
        # The whole 'new' pool, not just get_unscored(): the dealbreaker
        # filter is free, so it re-runs over everyone even if a job was
        # already scored before extraction filled in structured_data or the
        # questionnaire changed. The LLM-scoring loop below still only
        # processes jobs that genuinely have no score yet.
        full_pool = job_repository.get_new_with_descriptions() + job_repository.get_dealbreaker_rejected_with_descriptions()
        rescore_all = False

    if not full_pool:
        logger.info("No jobs to evaluate.")
        return {"jobs_scored": 0}

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
        return {"jobs_scored": 0, "jobs_auto_rejected": dealbreaker_stats["auto_rejected"]}

    try:
        candidate_profile = load_active_profile()
    except ValueError as e:
        logger.error(str(e))
        return {"jobs_scored": 0}

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
    if not unscored_jobs:
        logger.info("No jobs left to score after dealbreaker filter.")
        return {"jobs_scored": 0, "jobs_auto_rejected": dealbreaker_stats["auto_rejected"]}

    logger.info(f"Evaluating {len(unscored_jobs)} job(s)...")
    if learned_preferences:
        logger.info(f"Preference profile active ({latest_preference['applied_count']} applied, {latest_preference['rejected_count']} rejected)")
        logger.info(f"Grounding examples: {len(positive_examples)} applied, {len(negative_examples)} rejected")
    else:
        logger.info(f"No preference profile, few-shot: {len(positive_examples)} applied, {len(negative_examples)} rejected")
    logger.info("=" * 50)

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
