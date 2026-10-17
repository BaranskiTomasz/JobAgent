import logging
import time

import anthropic

logger = logging.getLogger(__name__)

# Worth retrying: transient, a network blip, a rate limit, or the API's own
# 5xx (this is also how Anthropic's 529 "overloaded" error surfaces, since the
# SDK maps any 5xx status to InternalServerError). Everything else (bad
# request, auth, permission, not found, a malformed request) fails identically
# on a second attempt, so retrying it would just waste the wait.
_RETRYABLE = (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError, anthropic.InternalServerError)


def call_with_retry(
    fn,
    *,
    label: str,
    max_attempts: int = 3,
    base_delay: float = 30,
    retryable_errors: tuple[type[Exception], ...] = _RETRYABLE,
    retry_if=None,
):
    # Retries a transient failure with linear backoff (base_delay,
    # 2*base_delay, ...); raises immediately for a non-transient error, or
    # the last exception once every attempt is exhausted. The default typed
    # errors are Anthropic's; callers can pass another provider's equivalent
    # transient error types while keeping the same retry behavior.
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            if not isinstance(e, retryable_errors) and not (retry_if and retry_if(e)):
                raise
            if attempt == max_attempts - 1:
                raise
            wait = base_delay * (attempt + 1)
            logger.warning(
                f"{label} failed ({type(e).__name__}: {e}), retrying in {wait}s "
                f"(attempt {attempt + 1}/{max_attempts})..."
            )
            time.sleep(wait)
