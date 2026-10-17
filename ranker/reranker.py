import logging
from collector.utils import build_excerpt
from embeddings.client import VoyageClient
from embeddings.client import voyage_retryable_errors
from ranker.retry import call_with_retry

logger = logging.getLogger(__name__)

# Voyage rerank-2 accepts far more than this; the cap bounds the fallback
# path's unbounded raw-text concatenation, not an API limit.
_MAX_QUERY_CHARS = 2000

_client: VoyageClient | None = None


def _get_client() -> VoyageClient:
    global _client
    if _client is None:
        _client = VoyageClient()
    return _client


def rerank_jobs(jobs: list[dict], query: str, top_k: int = 20) -> list[dict]:
    # Falls back to original order on failure; those jobs are marked
    # _rerank_unreliable so callers re-fusing rerank_score as an RRF leg know
    # not to treat it as a real signal.
    if not jobs:
        return []

    query_text = (query or "").strip()
    if not query_text:
        # A generic placeholder query would still produce a rerank_score that
        # looks considered but isn't, worse than not reranking at all. Skip
        # the call and preserve embedding-score order instead.
        logger.warning("rerank_jobs called with no candidate query (e.g. no CV uploaded yet), skipping cross-encoder rerank")
        for job in jobs:
            job.setdefault("rerank_score", job.get("_embedding_score") or 0.0)
            job["_rerank_unreliable"] = True
        return jobs[:top_k]

    client = _get_client()
    query_text = query_text[:_MAX_QUERY_CHARS]

    documents = []
    for job in jobs:
        desc = build_excerpt(job.get("description"), job.get("source")).replace("\n", " ")
        documents.append(f"{job['title']} at {job['company']}\n{desc}")

    try:
        results = call_with_retry(
            lambda: client.rerank(query=query_text, documents=documents, top_k=min(top_k, len(documents))),
            label="Voyage rerank",
            retryable_errors=voyage_retryable_errors(),
        )
    except Exception as e:
        logger.error(f"Voyage rerank failed: {e}, falling back to embedding order")
        for job in jobs:
            job.setdefault("rerank_score", job.get("_embedding_score", 0.0))
            job["_rerank_unreliable"] = True
        return jobs[:top_k]

    reranked = []
    for r in results:
        reranked.append({**jobs[r["index"]], "rerank_score": r["score"]})

    return reranked
