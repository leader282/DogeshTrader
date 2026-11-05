"""Rate limiters.

Two flavours:
* :class:`TokenBucket` - non-blocking, returns False if no tokens available.
* :class:`RateLimiter` - blocking, sleeps until a token is available.

Both are time-source-agnostic so they can be driven by a :class:`Clock` (e.g.
in backtests) instead of wall-clock time.
"""

from __future__ import annotations

import threading
import time as _time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class TokenBucket:
    """Classic token bucket with float precision.

    rate_per_sec * elapsed tokens are credited continuously, capped at ``burst``.
    """

    rate_per_sec: float
    burst: int
    time_fn: Callable[[], float] = _time.monotonic

    def __post_init__(self) -> None:
        self._tokens: float = float(self.burst)
        self._last: float = self.time_fn()
        self._lock = threading.RLock()

    def _refill(self) -> None:
        now = self.time_fn()
        elapsed = now - self._last
        if elapsed < 0:
            # The time source moved backwards (e.g. a replay clock restarting).
            # Re-anchor, or the bucket would never refill until it caught up.
            self._last = now
            return
        if elapsed == 0:
            return
        self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate_per_sec)
        self._last = now

    def try_acquire(self, cost: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens + 1e-9 >= cost:
                self._tokens -= cost
                return True
            return False

    def tokens(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens


class RateLimiter:
    """Blocking wrapper around :class:`TokenBucket`."""

    def __init__(
        self,
        rate_per_sec: float,
        burst: int,
        sleep_fn: Callable[[float], None] = _time.sleep,
        time_fn: Callable[[], float] = _time.monotonic,
    ) -> None:
        self._bucket = TokenBucket(rate_per_sec=rate_per_sec, burst=burst, time_fn=time_fn)
        self._sleep = sleep_fn

    def acquire(self, cost: float = 1.0, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else self._bucket.time_fn() + timeout
        while True:
            if self._bucket.try_acquire(cost):
                return True
            if deadline is not None and self._bucket.time_fn() >= deadline:
                return False
            # Sleep for the time required to accrue 1 token.
            wait = max(0.001, cost / max(self._bucket.rate_per_sec, 1e-3))
            self._sleep(min(wait, 0.1))


__all__ = ["RateLimiter", "TokenBucket"]
