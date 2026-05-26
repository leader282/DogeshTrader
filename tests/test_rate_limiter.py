"""Token bucket / rate limiter tests."""

from __future__ import annotations

from dogeshtrader.execution.rate_limiter import RateLimiter, TokenBucket


def test_token_bucket_starts_full() -> None:
    now = [0.0]
    bucket = TokenBucket(rate_per_sec=5.0, burst=5, time_fn=lambda: now[0])
    for _ in range(5):
        assert bucket.try_acquire()
    assert not bucket.try_acquire()


def test_token_bucket_refills_with_time() -> None:
    now = [0.0]
    bucket = TokenBucket(rate_per_sec=2.0, burst=2, time_fn=lambda: now[0])
    assert bucket.try_acquire()
    assert bucket.try_acquire()
    assert not bucket.try_acquire()
    now[0] = 1.0  # 1 second later -> 2 tokens
    assert bucket.try_acquire()
    assert bucket.try_acquire()
    assert not bucket.try_acquire()


def test_rate_limiter_blocking_acquires() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep_fn(s: float) -> None:
        sleeps.append(s)
        now[0] += s

    limiter = RateLimiter(rate_per_sec=10.0, burst=1, sleep_fn=sleep_fn, time_fn=lambda: now[0])
    assert limiter.acquire()
    assert limiter.acquire()  # forces at least one sleep
    assert sleeps  # blocking did happen
