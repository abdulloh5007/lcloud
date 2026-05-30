"""In-process TTL cache for read-heavy endpoints.

Why in-process (not Redis):
- LCloud runs as a single uvicorn process; a per-process dict is enough
  and has zero deployment overhead.
- If we ever scale to multiple workers, we'll switch the implementation
  here and the call sites won't need to change.

Usage:
    from lcloud.cache import cache

    cached = await cache.get(f"quota:{user_id}")
    if cached is not None:
        return cached
    fresh = await compute_quota(user_id)
    await cache.set(f"quota:{user_id}", fresh, ttl=10.0)
    return fresh

Invalidation on writes:
    await cache.delete(f"quota:{user_id}")
    await cache.delete_prefix(f"files:cloud={cloud_id}:")
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class TTLCache:
    """Simple async-safe TTL cache.

    All values are kept in a single dict guarded by an asyncio.Lock.
    Each entry is `(value, expires_at_monotonic)`. Expired entries
    are evicted on `.get()` lazily. Use `.cleanup()` from a periodic
    task to free memory eagerly if the cache grows.
    """

    def __init__(self, *, default_ttl: float = 60.0, max_entries: int = 10_000) -> None:
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self._misses += 1
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._data[key]
                self._misses += 1
                return None
            self._hits += 1
            return value

    async def set(self, key: str, value: Any, *, ttl: float | None = None) -> None:
        ttl_v = self._default_ttl if ttl is None else ttl
        async with self._lock:
            # Soft cap on size: evict oldest when over the limit.
            if len(self._data) >= self._max_entries and key not in self._data:
                # Evict the entry whose expires_at is earliest.
                oldest = min(self._data, key=lambda k: self._data[k][1])
                del self._data[oldest]
            self._data[key] = (value, time.monotonic() + ttl_v)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete all keys starting with `prefix`. Returns number deleted."""
        async with self._lock:
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                del self._data[k]
            return len(keys)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    async def stats(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "size": len(self._data),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": (
                    self._hits / max(1, self._hits + self._misses)
                ),
            }

    async def cleanup_expired(self) -> int:
        """Eagerly evict expired entries. Call from a background task if needed."""
        now = time.monotonic()
        async with self._lock:
            keys = [k for k, (_, exp) in self._data.items() if now > exp]
            for k in keys:
                del self._data[k]
            return len(keys)


# Singleton — shared across the app.
cache = TTLCache(default_ttl=60.0, max_entries=10_000)


# ----------------------------------------------------------- key helpers

# Centralizing the key naming so invalidation calls can't typo a prefix.


def k_user_me(user_id: int) -> str:
    return f"me:{user_id}"


def k_user_quota(user_id: int) -> str:
    return f"quota:{user_id}"


def k_user_clouds(user_id: int, role: str) -> str:
    # Admin sees ALL clouds → separate key so admin/user caches don't poison
    return f"clouds:{role}:{user_id}"


def k_files_in_cloud(cloud_id: int, user_id: int, role: str, limit: int, offset: int) -> str:
    return f"files:cloud={cloud_id}:{role}:{user_id}:l={limit}:o={offset}"


# ----------------------------------------------------------- invalidators


async def invalidate_user_quota(user_id: int) -> None:
    await cache.delete(k_user_quota(user_id))


async def invalidate_user_me(user_id: int) -> None:
    await cache.delete(k_user_me(user_id))


async def invalidate_user_clouds(user_id: int) -> None:
    """Invalidate caller's view AND any admin's view (admin sees all)."""
    await cache.delete(k_user_clouds(user_id, "user"))
    await cache.delete(k_user_clouds(user_id, "admin"))
    # Admin caches keyed by admin user_id — wipe all admin entries.
    await cache.delete_prefix("clouds:admin:")


async def invalidate_files_in_cloud(cloud_id: int) -> None:
    """File list pages for this cloud (any pagination, any role)."""
    await cache.delete_prefix(f"files:cloud={cloud_id}:")


__all__ = [
    "TTLCache",
    "cache",
    "invalidate_files_in_cloud",
    "invalidate_user_clouds",
    "invalidate_user_me",
    "invalidate_user_quota",
    "k_files_in_cloud",
    "k_user_clouds",
    "k_user_me",
    "k_user_quota",
]
