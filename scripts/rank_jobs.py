"""
Re-rank the entire active ('new') job pool using the full AI pipeline:
1. Semantic similarity (Voyage embeddings), broad recall
2. Voyage cross-encoder rerank (top-50), precision
3. Claude Opus listwise ranking with extended thinking (top-20), final ordering

Runs over ALL 'new' jobs every time (not just newly-collected ones), so listwise_rank
stays comparable across the whole list instead of being scoped to one day's batch.
"""
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
# No local logger, this script prints its own output directly, but still
# needs this so log messages from imported modules (e.g. ranker/retry.py's
# retry warnings) actually reach stdout instead of going nowhere.
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

import api_client
from db.repositories import job_repository, preference_repository
from embeddings.indexer import embedding_text_hash, score_pool_by_similarity, index_jobs
from ranker.fusion import fuse_by_rrf
from ranker.reranker import rerank_jobs
from ranker.listwise import listwise_rank
from ranker.debate import debate_rank
from ranker.rank_cache import reuse_if_unchanged
from ranker.would_apply import compute_revocations, compute_would_apply
from ranker.exploration import compute_non_exploration_ranks, pick_exploration_slots, tag_exploration_picks
from evaluator.profile import load_active_profile, load_questionnaire_preferences, build_hyde_query
from evaluator.fingerprints import ranking_fingerprint
from config import EXPLORATION, RANKING, VOYAGE_EMBED_MODEL

try:
    candidate_profile = load_active_profile()
except ValueError as e:
    # Matches evaluator/runner.py's own no-CV behavior: stop, don't score/rank
    # anything, rather than silently proceeding with candidate_profile="", a
    # ranking built with zero candidate context used to produce real-looking
    # listwise_rank/rank_reason output indistinguishable from a genuinely
    # personalized run. Jobs stay visible as 'new'/unranked instead.
    print(f"{e} Skipping ranking.")
    sys.exit(0)

latest_pref = preference_repository.get_latest()
preferences = latest_pref["signals"] if latest_pref else []
questionnaire = load_questionnaire_preferences()
retrieval_query = build_hyde_query(candidate_profile, questionnaire)

RANKING_POOL_LIMIT = 2000
jobs = job_repository.get_jobs_for_ranking(limit=RANKING_POOL_LIMIT)
if not jobs:
    print("No active jobs to rank.")
    sys.exit(0)
if len(jobs) == RANKING_POOL_LIMIT:
    print(f"WARNING: active job pool hit the safety cap ({RANKING_POOL_LIMIT}), oldest 'new' jobs beyond this were not considered this run.")

print(f"Processing {len(jobs)} active job(s)...")

# Step 1: Index any missing embeddings
embedding_metadata = api_client.get("/api/embeddings/metadata").json()
unindexed = [
    job for job in jobs
    if embedding_metadata.get(job["id"]) != {
        "model": VOYAGE_EMBED_MODEL,
        "text_hash": embedding_text_hash(job),
    }
]
if unindexed:
    print(f"\nIndexing {len(unindexed)} new embedding(s)...")
    indexed = index_jobs(unindexed)
    print(f"  Indexed {indexed} job(s)")

# Step 2: Semantic similarity, max-sim kNN against individual applied vectors
# when there's applied history, else a single-vector cold-start fallback (see
# embeddings/indexer.py::score_pool_by_similarity for why max-sim replaced the
# old single-centroid approach).
job_ids = [j["id"] for j in jobs]
sim_scores, basis = score_pool_by_similarity(job_ids, retrieval_query)
if basis:
    print(f"\nScoring by semantic similarity to {basis}...")
    for job in jobs:
        job["_embedding_score"] = sim_scores.get(job["id"], 0.0)
    jobs_by_sim = sorted(jobs, key=lambda j: j["_embedding_score"], reverse=True)
    top_scores = [round(j["_embedding_score"], 3) for j in jobs_by_sim[:5]]
    print(f"  Top-5 similarity scores: {top_scores}")
else:
    print("\nNo applied jobs, CV profile, or questionnaire preferences, skipping semantic retrieval.")
    jobs_by_sim = jobs
    for job in jobs:
        job["_embedding_score"] = None

# Step 3: Voyage rerank (top-N)
# Exclude jobs already known (via the scorer) to be near-dealbreakers, no point
# spending Voyage/Opus calls ranking them, and it keeps them from crowding out
# real candidates in the top-N pools. Jobs not yet scored are kept (score is None).
min_score = RANKING["min_score_for_ranking"]
ranking_eligible = [j for j in jobs_by_sim if j.get("score") is None or j["score"] > min_score]
excluded_low_score = len(jobs_by_sim) - len(ranking_eligible)
if excluded_low_score:
    print(f"\nExcluding {excluded_low_score} job(s) with score <= {min_score} from the rerank/listwise pool")

# RRF instead of pure embedding-rank truncation: a job the LLM scorer rated
# highly (e.g. 9/10) but with only average cosine similarity used to never
# surface here, the min_score exclusion above was the ONLY place the
# scorer's score ever affected this pool; ordering into the top-N was 100%
# cosine similarity until now.
fused = fuse_by_rrf(ranking_eligible)
rerank_pool = fused[:RANKING["top_n_rerank"]]
if len(rerank_pool) > 1:
    print(f"\nReranking top-{len(rerank_pool)} with Voyage cross-encoder...")
    # top_k=len(rerank_pool), not top_n_listwise, Voyage scores every pair
    # regardless of top_k (this doesn't cost more), and the fuse below needs a
    # rerank_score on the whole pool to pick the top-N itself, rather than
    # trusting Voyage's own cut as the final word on who reaches listwise ranking.
    rerank_pool = rerank_jobs(rerank_pool, retrieval_query, top_k=len(rerank_pool))
    print(f"  Got {len(rerank_pool)} after reranking")
    if not any(j.get("_rerank_unreliable") for j in rerank_pool):
        rerank_pool = fuse_by_rrf(rerank_pool, extra_rank_field="rerank_score")
else:
    for j in rerank_pool:
        j["rerank_score"] = j.get("_embedding_score")

# Step 4: Listwise Opus ranking + debate review (top-N), skipped when the
# candidate set is identical to what was ranked last run, since re-running
# Opus/debate against the exact same jobs would just reproduce the same
# reasoning at full cost (the one real repeated waste flagged by the audit).
listwise_pool = rerank_pool[:RANKING["top_n_listwise"]]

# Exploration slots: a few jobs from OUTSIDE the top-N pool (everything the
# deterministic pipeline, embedding similarity, RRF, cross-encoder rerank,
# didn't already surface) get a real Opus + debate look anyway, extending the
# listwise pool rather than displacing real top-N candidates. See
# ranker/exploration.py for why this is seeded by date, not per-run.
listwise_pool_ids = {j["id"] for j in listwise_pool}
exploration_candidates = [j for j in fused if j["id"] not in listwise_pool_ids]
exploration_picks = pick_exploration_slots(exploration_candidates, EXPLORATION["slots_per_day"])
if exploration_picks:
    print(f"\nExploration: adding {len(exploration_picks)} job(s) from outside the top-{RANKING['top_n_listwise']} pool")
    listwise_pool = listwise_pool + exploration_picks
exploration_ids = {j["id"] for j in exploration_picks}

current_ranking_fingerprint = ranking_fingerprint(listwise_pool, candidate_profile, questionnaire, preferences)
reused = reuse_if_unchanged(listwise_pool, jobs, current_ranking_fingerprint)
if reused is not None:
    if reused:
        print(f"\nTop-{len(reused)} candidate set unchanged since last run, reusing previous listwise + debate results")
    ranked = reused
else:
    print(f"\nListwise ranking {len(listwise_pool)} job(s) with Claude Opus + extended thinking...")
    ranked = listwise_rank(listwise_pool, candidate_profile, preferences, questionnaire)
    print(f"\nDebate review of top-{len(ranked)} with a second model...")
    ranked = debate_rank(ranked, candidate_profile, preferences, questionnaire)
    if exploration_ids:
        tag_exploration_picks(ranked, exploration_ids)

# Step 4b: Would-apply flag, phase 1 of the auto-apply plan (flag-and-validate
# only, never sends anything). See ranker/would_apply.py for the gate logic.
# Gated on the rank a job would have had with exploration picks removed, not
# its raw listwise_rank, so an exploration pick landing ahead of a real
# candidate can't push that candidate past rank_ceiling and cost it its slot.
non_exploration_ranks = compute_non_exploration_ranks(ranked, exploration_ids)
would_apply_items = []
would_apply_count = 0
for job in ranked:
    effective_rank = non_exploration_ranks.get(job["id"])
    flagged, reason = compute_would_apply(job.get("score"), job.get("debate_flag"), effective_rank)
    would_apply_count += flagged
    would_apply_items.append({"job_id": job["id"], "would_apply": flagged, "reason": reason})
if would_apply_items:
    job_repository.update_would_apply_batch(would_apply_items)
if would_apply_count:
    print(f"\nWould-apply: flagged {would_apply_count}/{len(ranked)} job(s) for validation")

# Revoke would_apply for jobs that dropped out of this run's would-apply set.
# Guarded on `ranked` being non-empty: an empty ranked list means nothing was
# actually re-evaluated this run (e.g. every job filtered out by min_score),
# not that every previously-flagged job genuinely stopped qualifying.
if ranked:
    still_flagged_ids = {item["job_id"] for item in would_apply_items if item["would_apply"]}
    revocations = compute_revocations(jobs, still_flagged_ids)
    if revocations:
        job_repository.update_would_apply_batch(revocations)
        print(f"\nWould-apply: revoked {len(revocations)} job(s) no longer in this run's would-apply set")

# Step 5: Save ranking results, one batched request for the whole pool (ranked
# jobs plus everything outside the listwise pool) instead of one PATCH per job.
ranking_items = [
    {
        "job_id": job["id"],
        "embedding_score": job.get("_embedding_score"),
        "rerank_score": job.get("rerank_score"),
        "listwise_rank": job.get("listwise_rank"),
        "rank_reason": job.get("rank_reason"),
        "debate_flag": job.get("debate_flag"),
        "debate_note": job.get("debate_note"),
        "fingerprint": current_ranking_fingerprint,
    }
    for job in ranked
]

ranked_ids = {j["id"] for j in ranked}
ranking_items += [
    {
        "job_id": job["id"],
        "embedding_score": job.get("_embedding_score"),
        "rerank_score": job.get("rerank_score"),
        "listwise_rank": None,
        "fingerprint": None,
    }
    for job in jobs_by_sim
    if job["id"] not in ranked_ids
]

if ranking_items:
    job_repository.update_ranking_scores_batch(ranking_items)

print(f"\nDone. Listwise ranked: {len(ranked)} | Embedding-scored only: {len(jobs) - len(ranked)}")
if ranked:
    print("\nTop 5:")
    for job in ranked[:5]:
        print(f"  #{job['listwise_rank']} {job['title']} @ {job['company']}, {job.get('rank_reason', '')[:80]}")
