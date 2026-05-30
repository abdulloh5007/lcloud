"""Tests for the WorkerPool concurrency limiter."""

from __future__ import annotations

import asyncio

import pytest

from lcloud.workers.pool import WorkerPool


def test_invalid_max_workers() -> None:
    with pytest.raises(ValueError):
        WorkerPool(max_workers=0)
    with pytest.raises(ValueError):
        WorkerPool(max_workers=-1)


@pytest.mark.asyncio
async def test_pool_caps_concurrency() -> None:
    pool = WorkerPool(max_workers=3)
    counter = {"now": 0, "max": 0}
    started = asyncio.Event()
    started_count = 0

    async def job() -> int:
        nonlocal started_count
        counter["now"] += 1
        counter["max"] = max(counter["max"], counter["now"])
        started_count += 1
        if started_count >= 3:
            started.set()
        try:
            await asyncio.sleep(0.05)
        finally:
            counter["now"] -= 1
        return 42

    tasks = [asyncio.create_task(pool.submit(job())) for _ in range(10)]
    results = await asyncio.gather(*tasks)
    assert all(r == 42 for r in results)
    assert counter["max"] <= 3
    # high_water should reflect the observed cap
    assert pool.high_water <= 3
    assert pool.high_water >= 1


@pytest.mark.asyncio
async def test_pool_propagates_exception() -> None:
    pool = WorkerPool(max_workers=2)

    async def fails() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await pool.submit(fails())


@pytest.mark.asyncio
async def test_pool_active_decrements_on_exception() -> None:
    pool = WorkerPool(max_workers=1)

    async def fails() -> None:
        raise RuntimeError("nope")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await pool.submit(fails())
    # Pool should not be permanently held by the failed jobs
    assert pool.active == 0


@pytest.mark.asyncio
async def test_singleton_lifecycle() -> None:
    from lcloud.workers.pool import (
        get_worker_pool,
        init_worker_pool,
        reset_worker_pool,
    )

    reset_worker_pool()
    with pytest.raises(RuntimeError, match="not initialised"):
        get_worker_pool()

    from lcloud.config import Settings

    s = Settings(_env_file=None, lc_max_workers=4)
    pool = init_worker_pool(s)
    assert pool.max_workers == 4
    assert get_worker_pool() is pool

    reset_worker_pool()
    with pytest.raises(RuntimeError):
        get_worker_pool()
