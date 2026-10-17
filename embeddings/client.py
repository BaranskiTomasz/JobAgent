import logging

from config import VOYAGE_API_KEY, VOYAGE_EMBED_MODEL, VOYAGE_RERANK_MODEL

logger = logging.getLogger(__name__)


def voyage_retryable_errors() -> tuple[type[Exception], ...]:
    """Return the Voyage SDK's typed transient failures without importing it at startup."""
    try:
        from voyageai.error import APIConnectionError, RateLimitError, ServiceUnavailableError, Timeout
    except ImportError:
        return ()

    return APIConnectionError, RateLimitError, ServiceUnavailableError, Timeout


def _require_voyage():
    if not VOYAGE_API_KEY:
        raise RuntimeError("VOYAGE_API_KEY not set, add it to .env to use embedding/rerank features.")
    try:
        import voyageai  # noqa: F401
    except ImportError:
        raise RuntimeError("voyageai package not installed. Run: pip install voyageai")


class VoyageClient:
    def __init__(self):
        _require_voyage()
        import voyageai
        self._client = voyageai.Client(api_key=VOYAGE_API_KEY)

    def embed(self, texts: list[str], input_type: str = "document") -> list[list[float]]:
        """Embed a list of texts. input_type: 'document' for indexing, 'query' for retrieval."""
        result = self._client.embed(texts, model=VOYAGE_EMBED_MODEL, input_type=input_type)
        try:
            from db.repositories.usage_repository import log_voyage_embed
            log_voyage_embed(getattr(result, "total_tokens", len(texts) * 512))
        except Exception:
            pass
        return result.embeddings

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[dict]:
        """Cross-encoder rerank. Returns list of {index, score} sorted by score desc."""
        result = self._client.rerank(query, documents, model=VOYAGE_RERANK_MODEL, top_k=top_k)
        try:
            from db.repositories.usage_repository import log_voyage_rerank
            # Voyage bills rerank per token processed (query + documents), not per query,
            # log the real total_tokens the API reports rather than a document-count proxy.
            log_voyage_rerank(getattr(result, "total_tokens", len(documents) * 200))
        except Exception:
            pass
        return [{"index": r.index, "score": r.relevance_score} for r in result.results]
