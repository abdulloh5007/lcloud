"""Async token-bucket rate limiter + FloodWait-aware retry helper.

The token bucket gates *outgoing* MTProto request rate so we don't
hammer Telegram. `call_with_floodwait_retry` adds layered protection:
on Telethon `FloodWaitError`, we honour the requested wait (clamped by
`max_floodwait_sec`) and retry up to `max_retries` times.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from telethon.errors import FloodWaitError

from lcloud.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AsyncTokenBucket:
    """Async token bucket. `acquire()` awaits until a token is available."""

    def __init__(self, *, rate_per_sec: float, burst: int) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        if burst < 1:
            raise ValueError("burst must be >= 1")
        self._rate = float(rate_per_sec)
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def capacity(self) -> int:
        return int(self._capacity)

    async def acquire(self, n: float = 1.0) -> None:
        """Block until `n` tokens are available; consume them and return."""
        if n <= 0:
            raise ValueError("n must be positive")
        if n > self._capacity:
            raise ValueError(f"requested {n} tokens > capacity {self._capacity}")
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._last) * self._rate
                )
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                # Fall through: compute wait outside the lock so other
                # callers can observe refills concurrently.
                deficit = n - self._tokens
                wait = deficit / self._rate
            await asyncio.sleep(wait)


async def call_with_floodwait_retry(
    rl: AsyncTokenBucket,
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    max_floodwait_sec: int = 300,
) -> T:
    """Run `fn()` under `rl`; on `FloodWaitError`, sleep + retry up to N times.

    Raises `FloodWaitError` if Telegram requests a wait longer than
    `max_floodwait_sec` (caller decides whether to retry later) or if all
    retries are exhausted.
    """
    last_error: FloodWaitError | None = None
    for attempt in range(max_retries):
        await rl.acquire()
        try:
            return await fn()
        except FloodWaitError as exc:
            seconds = getattr(exc, "seconds", 0) or 0
            if seconds > max_floodwait_sec:
                logger.warning(
                    "FloodWait %ss exceeds max_floodwait_sec=%s; surfacing to caller",
                    seconds,
                    max_floodwait_sec,
                )
                raise
            wait = seconds + 1
            logger.warning(
                "FloodWait %ss (attempt %d/%d); sleeping %ss",
                seconds,
                attempt + 1,
                max_retries,
                wait,
            )
            last_error = exc
            await asyncio.sleep(wait)
    assert last_error is not None
    raise last_error


async def mtproto_call(
    client: Any, request: Any, *, max_retries: int = 3
) -> Any:
    """Convenience wrapper: run a Telethon TL request under the global limiter
    + FloodWait retry, with the configured max-floodwait cap.

    `client` is a Telethon `TelegramClient`; `request` is a TL function /
    request object. Return type is `Any` because each TL request returns
    a different TL type — callers know what they sent.
    """
    limiter = get_mtproto_limiter()
    settings = get_settings()
    return await call_with_floodwait_retry(
        limiter,
        lambda: client(request),
        max_retries=max_retries,
        max_floodwait_sec=settings.lc_mtproto_max_floodwait_sec,
    )


# ------------------------------------------------------------------ singleton

_limiter: AsyncTokenBucket | None = None


def init_mtproto_limiter(settings: Settings | None = None) -> AsyncTokenBucket:
    """Create / replace the process-wide MTProto limiter from settings."""
    global _limiter
    s = settings or get_settings()
    _limiter = AsyncTokenBucket(
        rate_per_sec=s.lc_mtproto_rate_per_sec, burst=s.lc_mtproto_burst
    )
    logger.info(
        "MTProto rate limiter initialised; %s/s burst=%s",
        s.lc_mtproto_rate_per_sec,
        s.lc_mtproto_burst,
    )
    return _limiter


def get_mtproto_limiter() -> AsyncTokenBucket:
    if _limiter is None:
        raise RuntimeError(
            "MTProto rate limiter not initialised; call init_mtproto_limiter"
        )
    return _limiter


def reset_mtproto_limiter() -> None:
    """Tests only — clear the singleton."""
    global _limiter
    _limiter = None
