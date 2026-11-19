import logging
from datetime import datetime, timezone

import api_client
from config import MODEL_COSTS, CACHE_WRITE_MULTIPLIER, CACHE_READ_MULTIPLIER

logger = logging.getLogger(__name__)


def _calc_cost(
    model: str, input_tokens: int, output_tokens: int = 0,
    cache_creation_tokens: int = 0, cache_read_tokens: int = 0,
    discount: float = 1.0,
) -> float:
    input_rate, output_rate = MODEL_COSTS.get(model, (0.0, 0.0))
    cost = (
        input_tokens * input_rate
        + output_tokens * output_rate
        + cache_creation_tokens * input_rate * CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * input_rate * CACHE_READ_MULTIPLIER
    )
    return cost / 1_000_000 * discount


def log_usage(
    model: str, module: str, input_tokens: int, output_tokens: int = 0,
    cache_creation_tokens: int = 0, cache_read_tokens: int = 0,
    discount: float = 1.0,
) -> None:
    cost = _calc_cost(
        model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
        discount,
    )
    try:
        api_client.post("/api/usage", json={
            "model": model, "module": module,
            # Cache tokens folded into input_tokens here, JobAgentWeb's usage_log has
            # no separate column for them, and this keeps the reported token totals
            # matching real API throughput instead of undercounting cached requests.
            "input_tokens": input_tokens + cache_creation_tokens + cache_read_tokens,
            "output_tokens": output_tokens, "cost_usd": cost,
        })
    except Exception:
        logger.warning(f"Failed to log usage ({module}/{model})", exc_info=True)  # never block the main flow


def log_anthropic(response, module: str, model: str, discount: float = 1.0) -> None:
    # Includes prompt-cache read/write tokens: response.usage.input_tokens
    # alone excludes those and would undercount every cached call.
    try:
        log_usage(
            model, module, response.usage.input_tokens, response.usage.output_tokens,
            cache_creation_tokens=response.usage.cache_creation_input_tokens or 0,
            cache_read_tokens=response.usage.cache_read_input_tokens or 0,
            discount=discount,
        )
    except Exception:
        logger.warning(f"Failed to log Anthropic usage ({module}/{model})", exc_info=True)


def log_voyage_embed(total_tokens: int) -> None:
    log_usage("voyage-3-large", "embed", total_tokens)


def log_voyage_rerank(total_tokens: int) -> None:
    """Voyage rerank pricing is per token processed (query + documents), $0.05/1M tokens."""
    log_usage("rerank-2", "rerank", total_tokens)


def get_summary() -> dict:
    try:
        return api_client.get("/api/usage/summary").json()
    except Exception:
        logger.warning("Failed to fetch usage summary, returning zeroed fallback", exc_info=True)
        return {"today_cost_usd": 0, "today_tokens": 0, "total_cost_usd": 0, "total_tokens": 0, "cost_per_100_usd": None}


def record_run_summary(run_label: str, started_at: str) -> None:
    # Aggregation happens server-side; this just tells JobAgentWeb which run
    # and window to snapshot into a durable, per-run cost record.
    api_client.post("/api/usage/run-summary", json={"run_label": run_label, "started_at": started_at})


def get_cost_per_100() -> float | None:
    return get_summary().get("cost_per_100_usd")


def get_history() -> list[dict]:
    return api_client.get("/api/usage/history").json()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
