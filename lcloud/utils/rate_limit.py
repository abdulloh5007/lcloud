"""Token-bucket rate limiter (per-key, in-process).

Thread-safe via a single `Lock`. Buckets accumulate over time and never
exceed `capacity`. `try_acquire` returns False if the bucket is empty
without consuming any tokens.
"""

from __future__ import annotations

import time
from threading import Lock


class RateLimiter:
    def __init__(self, *, capacity: int, refill_seconds: float) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if refill_seconds <= 0:
            raise ValueError("refill_seconds must be > 0")
        self._capacity = float(capacity)
        self._refill_rate = capacity / refill_seconds  # tokens / sec
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._lock = Lock()

    def try_acquire(self, key: str, cost: float = 1.0) -> bool:
        with self._lock:
            now = time.monotonic()
            tokens, last = self._buckets.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._refill_rate)
            if tokens < cost:
                self._buckets[key] = (tokens, now)
                return False
            tokens -= cost
            self._buckets[key] = (tokens, now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
