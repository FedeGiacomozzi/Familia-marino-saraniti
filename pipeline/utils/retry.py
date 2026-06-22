"""Retry helper for transient external API failures (Whisper, Claude)."""
import logging
import time

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS = frozenset([429, 500, 502, 503, 504])


def _is_transient(exc: BaseException) -> bool:
    """True for errors worth retrying: timeouts, network blips, rate limits, 5xx."""
    try:
        import openai
        if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
            return True
        if isinstance(exc, openai.APIStatusError) and exc.status_code in _TRANSIENT_STATUS:
            return True
    except ImportError:
        pass
    try:
        import anthropic as ant
        if isinstance(exc, (ant.APITimeoutError, ant.APIConnectionError)):
            return True
        if isinstance(exc, ant.APIStatusError) and exc.status_code in _TRANSIENT_STATUS:
            return True
    except ImportError:
        pass
    return False


def call_with_retry(
    fn,
    *args,
    label: str = "",
    max_attempts: int = 3,
    base_delay: float = 2.0,
    **kwargs,
):
    """
    Call fn(*args, **kwargs) with exponential backoff on transient errors.
    Delays: 2s → 4s → 8s. Permanent errors (401, 400) fail immediately without retry.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == max_attempts or not _is_transient(exc):
                if attempt > 1:
                    logger.error(
                        "[retry:%s] fallo definitivo tras %d intentos: %s",
                        label, attempt, exc,
                    )
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "[retry:%s] intento %d/%d falló (%s), reintentando en %.0fs",
                label, attempt, max_attempts, type(exc).__name__, delay,
            )
            time.sleep(delay)
