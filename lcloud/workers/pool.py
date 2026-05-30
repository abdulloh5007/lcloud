"""Concurrency-limited async runner ("worker pool" via Semaphore).

`asyncio.Queue` + N background tasks would also work, but for our use case
— callers (e.g., HTTP handlers) want the awaited result of a single
coroutine — a Semaphore-gated `submit()` is simpler and behaviour-equivalent:
the caller's task is what executes the coroutine, just under a global cap.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

from lcloud.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class WorkerPool:
    """Cap concurrent coroutines at `max_workers`. Calls run in caller's task."""

    def __init__(self, max_workers: int = 10) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self._max_workers = max_workers
        self._sem = asyncio.Semaphore(max_workers)
        self._active = 0
        self._high_water = 0

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def active(self) -> int:
        return self._active

    @property
    def high_water(self) -> int:
        """Maximum concurrent jobs ever observed (debug / metrics)."""
        return self._high_water

    async def submit(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run `coro` under the concurrency cap; return its awaited result."""
        async with self._sem:
            self._active += 1
            if self._active > self._high_water:
                self._high_water = self._active
            try:
                return await coro
            finally:
                self._active -= 1


# ------------------------------------------------------------------ singleton

_pool: WorkerPool | None = None


def init_worker_pool(settings: Settings | None = None) -> WorkerPool:
    """Create / replace the process-wide pool from settings (idempotent)."""
    global _pool
    s = settings or get_settings()
    _pool = WorkerPool(max_workers=s.lc_max_workers)
    logger.info("worker pool initialised; max_workers=%d", s.lc_max_workers)
    return _pool


def get_worker_pool() -> WorkerPool:
    if _pool is None:
        raise RuntimeError("WorkerPool not initialised; call init_worker_pool first")
    return _pool


def reset_worker_pool() -> None:
    """Tests only — clear the singleton."""
    global _pool
    _pool = None
