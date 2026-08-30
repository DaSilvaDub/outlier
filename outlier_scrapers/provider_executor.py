"""Shared retry executor for AI research desk provider calls.

Before this module existed, each provider runner (reasoning.py, gemini_research.py, ...)
hand-rolled its own N-attempt sleep loop around its API call. That duplicated
the same bounded-retry policy five times with five slightly different bugs.

The contract now: a provider's call function issues exactly one logical
attempt and raises ``ProviderCallError(retryable=...)`` (or lets a retryable
SDK exception through, per its own ``is_retryable`` predicate) on failure.
``execute_with_retry`` owns the loop, the backoff schedule, and the jitter.
No provider should independently implement a sleep loop after this.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ProviderCallError(Exception):
    """Raised by a provider call function to report one failed attempt.

    ``retryable=True`` means the executor should retry (subject to
    ``max_attempts``); ``retryable=False`` (the default) means the failure is
    terminal and should propagate immediately.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _default_is_retryable(exc: BaseException) -> bool:
    return bool(getattr(exc, "retryable", False))


def execute_with_retry(
    call: Callable[[], T],
    *,
    max_attempts: int,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 60.0,
    is_retryable: Callable[[BaseException], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> T:
    """Run ``call`` up to ``max_attempts`` times with bounded exponential backoff.

    ``call`` takes no arguments and issues exactly one logical provider
    attempt; it should raise on failure (a ``ProviderCallError`` or any
    exception ``is_retryable`` accepts). Non-retryable failures, and the
    final attempt's failure regardless of retryability, propagate as-is.

    Backoff doubles each attempt starting at ``base_delay_seconds``, capped at
    ``max_delay_seconds``, with +/-50% jitter so concurrent runners don't
    retry in lockstep.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    predicate = is_retryable or _default_is_retryable
    attempt = 0
    while True:
        attempt += 1
        try:
            return call()
        except Exception as exc:
            if attempt >= max_attempts or not predicate(exc):
                raise
            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            delay *= 0.5 + rng() / 2  # jitter: 50%-100% of the computed delay
            logger.warning(
                "Provider call attempt %d/%d failed (%s); retrying in %.1fs",
                attempt,
                max_attempts,
                type(exc).__name__,
                delay,
            )
            sleep(delay)
