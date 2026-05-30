"""Tests for the token-bucket rate limiter."""

from __future__ import annotations

import time

import pytest

from lcloud.utils.rate_limit import RateLimiter


def test_basic_capacity_then_refuse() -> None:
    rl = RateLimiter(capacity=3, refill_seconds=10.0)
    assert rl.try_acquire("ip1")
    assert rl.try_acquire("ip1")
    assert rl.try_acquire("ip1")
    assert not rl.try_acquire("ip1")


def test_independent_keys() -> None:
    rl = RateLimiter(capacity=1, refill_seconds=10.0)
    assert rl.try_acquire("a")
    assert not rl.try_acquire("a")
    # different key has its own bucket
    assert rl.try_acquire("b")


def test_refill_over_time() -> None:
    rl = RateLimiter(capacity=2, refill_seconds=0.1)
    assert rl.try_acquire("k")
    assert rl.try_acquire("k")
    assert not rl.try_acquire("k")
    time.sleep(0.12)  # > one full refill window
    assert rl.try_acquire("k")


def test_invalid_args() -> None:
    with pytest.raises(ValueError):
        RateLimiter(capacity=0, refill_seconds=10.0)
    with pytest.raises(ValueError):
        RateLimiter(capacity=1, refill_seconds=0.0)


def test_reset_clears_buckets() -> None:
    rl = RateLimiter(capacity=1, refill_seconds=10.0)
    assert rl.try_acquire("k")
    assert not rl.try_acquire("k")
    rl.reset()
    assert rl.try_acquire("k")
