"""Exponential-backoff retry for transient failures (pure, injectable clock).

The ``sleeper`` is injectable so tests assert the backoff schedule without ever
sleeping. Only ``retry_on`` exceptions are retried; anything else propagates
immediately (a permanent error shouldn't waste retries before hitting the DLQ).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from fossilrag.logging import get_logger

log = get_logger("worker.retry")

T = TypeVar("T")


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.1,
    sleeper: Callable[[float], None] = time.sleep,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Call ``fn``; on a ``retry_on`` error retry with delay base*2**(n-1)."""
    attempt = 0
    while True:
        try:
            return fn()
        except retry_on as exc:
            attempt += 1
            if attempt >= max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(
                "event=retry attempt=%d max=%d delay=%.3f error=%s",
                attempt,
                max_attempts,
                delay,
                type(exc).__name__,
            )
            if delay > 0:
                sleeper(delay)
