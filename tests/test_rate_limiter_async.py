"""Tests for AsyncTokenBucket + FloodWait retry helper."""

from __future__ import annotations

import asyncio
import time

import pytest
from telethon.errors import FloodWaitError

from lcloud.workers.rate_limiter import (
    AsyncTokenBucket,
    call_with_floodwait_retry,
)


def test_invalid_args() -> None:
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate_per_sec=0, burst=1)
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate_per_sec=10, burst=0)


@pytest.mark.asyncio
async def test_bucket_immediate_burst() -> None:
    rl = AsyncTokenBucket(rate_per_sec=1.0, burst=3)
    t0 = time.monotonic()
    for _ in range(3):
        await rl.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.05  # burst should be immediate


@pytest.mark.asyncio
async def test_bucket_blocks_when_empty_then_refills() -> None:
    rl = AsyncTokenBucket(rate_per_sec=20.0, burst=1)
    await rl.acquire()  # consumes the lone token
    t0 = time.monotonic()
    await rl.acquire()  # must wait ~50ms (1 token at 20/s)
    elapsed = time.monotonic() - t0
    assert 0.03 <= elapsed <= 0.30


@pytest.mark.asyncio
async def test_concurrent_acquirers_share_tokens_fairly() -> None:
    rl = AsyncTokenBucket(rate_per_sec=10.0, burst=2)
    started = time.monotonic()
    finished_at: list[float] = []

    async def grab() -> None:
        await rl.acquire()
        finished_at.append(time.monotonic() - started)

    await asyncio.gather(*[grab() for _ in range(6)])
    assert len(finished_at) == 6
    # First 2 ~immediate, then ~0.1s apart (10/s refill)
    finished_at.sort()
    assert finished_at[0] < 0.05
    assert finished_at[1] < 0.05
    assert finished_at[5] >= 0.30  # ~4 tokens at 10/s = 0.4s minimum


@pytest.mark.asyncio
async def test_acquire_more_than_capacity_raises() -> None:
    rl = AsyncTokenBucket(rate_per_sec=10.0, burst=2)
    with pytest.raises(ValueError):
        await rl.acquire(n=5)


@pytest.mark.asyncio
async def test_floodwait_retry_succeeds_after_one_wait() -> None:
    rl = AsyncTokenBucket(rate_per_sec=100.0, burst=10)
    calls = {"n": 0}

    async def fn() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            err = FloodWaitError(request=None)
            err.seconds = 0  # don't actually sleep
            raise err
        return "ok"

    result = await call_with_floodwait_retry(rl, fn, max_retries=3)
    assert result == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_floodwait_retry_exhausts() -> None:
    rl = AsyncTokenBucket(rate_per_sec=100.0, burst=10)

    async def fn() -> None:
        err = FloodWaitError(request=None)
        err.seconds = 0
        raise err

    with pytest.raises(FloodWaitError):
        await call_with_floodwait_retry(rl, fn, max_retries=2)


@pytest.mark.asyncio
async def test_floodwait_above_max_surfaces_immediately() -> None:
    rl = AsyncTokenBucket(rate_per_sec=100.0, burst=10)
    calls = {"n": 0}

    async def fn() -> None:
        calls["n"] += 1
        err = FloodWaitError(request=None)
        err.seconds = 600  # higher than our cap
        raise err

    with pytest.raises(FloodWaitError):
        await call_with_floodwait_retry(rl, fn, max_retries=5, max_floodwait_sec=300)
    assert calls["n"] == 1  # didn't retry


@pytest.mark.asyncio
async def test_singleton_lifecycle() -> None:
    from lcloud.config import Settings
    from lcloud.workers.rate_limiter import (
        get_mtproto_limiter,
        init_mtproto_limiter,
        reset_mtproto_limiter,
    )

    reset_mtproto_limiter()
    with pytest.raises(RuntimeError, match="not initialised"):
        get_mtproto_limiter()

    s = Settings(_env_file=None, lc_mtproto_rate_per_sec=15.0, lc_mtproto_burst=5)
    rl = init_mtproto_limiter(s)
    assert rl.capacity == 5
    assert get_mtproto_limiter() is rl

    reset_mtproto_limiter()
