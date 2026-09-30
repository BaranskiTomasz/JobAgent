import hashlib
import logging
import time

from config import VOYAGE_EMBED_MODEL
from collector.utils import build_excerpt
import api_client
from embeddings.client import VoyageClient

logger = logging.getLogger(__name__)

_client: VoyageClient | None = None

BATCH_SIZE = 128  # Voyage API max; safe at 600 RPM
BATCH_DELAY = 1   # seconds between batches (paid tier; increase to 22 if still on free tier)


def _get_client() -> VoyageClient:
    global _client
    if _client is None:
        _client = VoyageClient()
    return _client


def _job_to_text(job: dict) -> str:
    parts = [f"{job['title']} at {job['company']}"]
    if job.get("location"):
        parts.append(f"Location: {job['location']}")
    excerpt = build_excerpt(job.get("description"), job.get("source"))
    if excerpt:
        parts.append(excerpt[:2000])
    return "\n".join(parts)


def embedding_text_hash(job: dict) -> str:
    return hashlib.sha256(_job_to_text(job).encode()).hexdigest()


def _embed_with_retry(client: VoyageClient, texts: list[str], max_retries: int = 6) -> list:
    """Embed with exponential-backoff retry for rate-limit errors."""
    for attempt in range(max_retries):
        try:
            return client.embed(texts)
        except Exception as e:
            msg = str(e).lower()
            if "rate" in msg or "429" in msg or "limit" in msg:
                wait = max(22, BATCH_DELAY) * (2 ** min(attempt, 3))
                logger.warning(f"  Rate limit hit (attempt {attempt+1}), waiting {wait:.0f}s…")
                time.sleep(wait)
            else:
                raise
    return client.embed(texts)  # final attempt, let it raise


def index_jobs(jobs: list[dict]) -> int:
    """Embed jobs and store in job_embeddings (shared across every user). Returns count indexed."""
    if not jobs:
        return 0

    client = _get_client()
    indexed = 0
    n = len(jobs)

    for i in range(0, n, BATCH_SIZE):
        batch = jobs[i:i + BATCH_SIZE]
        texts = [_job_to_text(j) for j in batch]
        embeddings = _embed_with_retry(client, texts)

        api_client.post("/api/embeddings", json={
            "items": [
                {"job_id": job["id"], "embedding": emb, "model": VOYAGE_EMBED_MODEL,
                 "text_hash": hashlib.sha256(text.encode()).hexdigest()}
                for job, text, emb in zip(batch, texts, embeddings)
            ],
        })
        indexed += len(batch)

        done = min(i + BATCH_SIZE, n)
        logger.info(f"  Indexed {done}/{n} jobs")

        if done < n:
            time.sleep(BATCH_DELAY)

    return indexed


def _max_scores(job_ids: list[str], vectors: list[list[float]]) -> dict[str, float]:
    # Max cosine similarity per job_id across all given vectors. One
    # /api/embeddings/similarity call per vector; each returns only
    # {job_id: float}, never raw vectors, so this stays cheap per call.
    best: dict[str, float] = {}
    for vec in vectors:
        for job_id, score in score_by_similarity(job_ids, vec).items():
            if job_id not in best or score > best[job_id]:
                best[job_id] = score
    return best


def score_pool_by_similarity(job_ids: list[str], candidate_profile: str | None = None) -> tuple[dict[str, float], str | None]:
    # Returns ({job_id: score}, basis); basis is None when there's nothing to
    # score against (no applied history and no candidate_profile/HyDE text).
    #
    # With applied history: max-sim kNN, score(job) = max cosine-sim to ANY
    # individual applied vector, minus 0.3x max cosine-sim to any rejected
    # vector. A centroid over multi-modal applied history (e.g. some backend
    # roles, some data-engineering roles) can land in semantic no-man's-land
    # close to neither cluster, starving one side of the candidate's real
    # interests even though every individual applied job is a valid example.
    #
    # Without applied history: falls back to embedding candidate_profile (a
    # synthetic ideal-job-posting query from build_hyde_query) as a single
    # query vector, since max-sim needs multiple examples and there's nothing
    # to run kNN against with zero applied jobs.
    if not job_ids:
        return {}, None

    vectors = api_client.get("/api/embeddings/decision-vectors", params={"model": VOYAGE_EMBED_MODEL}).json()
    applied_vecs = vectors["applied"]

    if not applied_vecs:
        if not candidate_profile:
            return {}, None
        client = _get_client()
        [vec] = client.embed([candidate_profile], input_type="query")
        return score_by_similarity(job_ids, vec), "CV profile / questionnaire (no applied jobs yet)"

    max_applied = _max_scores(job_ids, applied_vecs)

    rejected_vecs = vectors["rejected"]
    if not rejected_vecs:
        return max_applied, "applied jobs (max-sim)"

    max_rejected = _max_scores(job_ids, rejected_vecs)
    scores = {job_id: score - 0.3 * max_rejected.get(job_id, 0.0) for job_id, score in max_applied.items()}
    return scores, "applied jobs (max-sim)"


def score_by_similarity(job_ids: list[str], ideal: list[float]) -> dict[str, float]:
    """Return cosine similarity for each job_id against the ideal vector.

    Computed server-side (JobAgentWeb has the vectors already) instead of
    fetching every raw vector over HTTP just to reduce each one to a single
    float locally: a 1024-dim vector is ~22 KB of JSON, so the old approach
    shipped tens of MB for a pool of a couple thousand jobs, and retried the
    whole transfer on any timeout, since api_client treats those as retryable."""
    if not job_ids or not ideal:
        return {}

    return api_client.post("/api/embeddings/similarity", json={"ideal": ideal, "job_ids": job_ids}).json()
