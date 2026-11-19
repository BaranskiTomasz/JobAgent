import hashlib
import logging
import time
from collections.abc import Iterator

from config import VOYAGE_EMBED_MODEL
from collector.utils import build_excerpt
import api_client
from embeddings.client import VoyageClient, voyage_retryable_errors
from ranker.retry import call_with_retry

logger = logging.getLogger(__name__)

_client: VoyageClient | None = None

BATCH_SIZE = 128  # Voyage API max; safe at 600 RPM
# Voyage's real per-request limit for voyage-3-large is 320k tokens (max 1000
# inputs/request) — https://docs.voyageai.com/docs/rate-limits. The len//4
# estimate below is a rough chars-per-token heuristic that can undercount 2-3x
# for non-Latin text (Cyrillic, Polish diacritics — this project scrapes
# DOU.ua/Djinni.co and itpracuj/theprotocol). 60_000 gives >5x headroom under
# normal (Latin-text) estimation; even at a worst-case 3x undercount that's
# ~180k real tokens, still under the 320k limit (~1.8x margin, not >5x — don't
# raise this value without re-checking that margin, or switch to
# voyageai.Client.count_tokens() for an exact count instead of a heuristic).
MAX_BATCH_TOKENS = 60_000
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
        parts.append(excerpt)
    return "\n".join(parts)


def embedding_text_hash(job: dict) -> str:
    return hashlib.sha256(_job_to_text(job).encode()).hexdigest()


def _embed_with_retry(client: VoyageClient, texts: list[str], input_type: str = "document") -> list:
    return call_with_retry(
        lambda: client.embed(texts, input_type=input_type),
        label="Voyage embedding",
        retryable_errors=voyage_retryable_errors(),
    )


def _job_batches(jobs_with_text: list[tuple[dict, str]]) -> Iterator[list[tuple[dict, str]]]:
    # Takes (job, text) pairs so _job_to_text() runs once per job in index_jobs(),
    # not again here just for the token estimate.
    batch = []
    estimated_tokens = 0

    for job, text in jobs_with_text:
        job_tokens = len(text) // 4
        if batch and (len(batch) >= BATCH_SIZE or estimated_tokens + job_tokens > MAX_BATCH_TOKENS):
            yield batch
            batch = []
            estimated_tokens = 0

        batch.append((job, text))
        estimated_tokens += job_tokens

    if batch:
        yield batch


def index_jobs(jobs: list[dict]) -> int:
    """Embed jobs and store in job_embeddings (shared across every user). Returns count indexed."""
    if not jobs:
        return 0

    client = _get_client()
    indexed = 0
    n = len(jobs)
    jobs_with_text = [(job, _job_to_text(job)) for job in jobs]

    for batch in _job_batches(jobs_with_text):
        texts = [text for _, text in batch]
        embeddings = _embed_with_retry(client, texts)

        api_client.post("/api/embeddings", json={
            "items": [
                {"job_id": job["id"], "embedding": emb, "model": VOYAGE_EMBED_MODEL,
                 "text_hash": hashlib.sha256(text.encode()).hexdigest()}
                for (job, text), emb in zip(batch, embeddings)
            ],
        })
        indexed += len(batch)

        done = indexed
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
        [vec] = _embed_with_retry(client, [candidate_profile], input_type="query")
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
