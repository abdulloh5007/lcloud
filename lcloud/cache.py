"""In-process TTL cache + optional Redis backend for read-heavy endpoints.

When `LC_REDIS_URL` env var is set (e.g. `redis://localhost:6379/0`),
all cache ops go through Redis — this lets you scale to multiple
uvicorn workers / replicas without poisoned reads.

When unset, falls back to in-memory dict (single process, single host).

Usage stays the same:
    from lcloud.cache import cache
    await cache.get("key")
    await cache.set("key", value, ttl=30)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


class TTLCache:
    """Async-safe TTL cache. In-process by default, Redis when configured."""

    def __init__(self, *, default_ttl: float = 60.0, max_entries: int = 10_000) -> None:
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0
        self._redis: Any = None
        self._redis_url = os.getenv("LC_REDIS_URL")

    async def _get_redis(self) -> Any:
        """Lazy-connect to Redis on first use; cache the connection."""
        if self._redis is not None or not self._redis_url:
            return self._redis
        try:
            import redis.asyncio as redis_async

            self._redis = redis_async.from_url(
                self._redis_url, decode_responses=True
            )
            await self._redis.ping()
            logger.info("TTLCache backed by Redis at %s", self._redis_url)
        except Exception as exc:
            logger.warning("Redis unavailable (%s); falling back to memory", exc)
            self._redis = None
            self._redis_url = None
        return self._redis

    async def get(self, key: str) -> Any | None:
        r = await self._get_redis()
        if r is not None:
            try:
                raw = await r.get(key)
                if raw is None:
                    self._misses += 1
                    return None
                self._hits += 1
                return json.loads(raw)
            except Exception as exc:
                logger.warning("Redis get failed: %s; falling back", exc)

        # In-memory fallback
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
        r = await self._get_redis()
        if r is not None:
            try:
                await r.set(key, json.dumps(value), ex=int(ttl_v))
                return
            except Exception as exc:
                logger.warning("Redis set failed: %s; falling back", exc)

        async with self._lock:
            if len(self._data) >= self._max_entries and key not in self._data:
                oldest = min(self._data, key=lambda k: self._data[k][1])
                del self._data[oldest]
            self._data[key] = (value, time.monotonic() + ttl_v)

    async def delete(self, key: str) -> None:
        r = await self._get_redis()
        if r is not None:
            try:
                await r.delete(key)
                return
            except Exception as exc:
                logger.warning("Redis delete failed: %s; falling back", exc)

        async with self._lock:
            self._data.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        r = await self._get_redis()
        if r is not None:
            try:
                # SCAN to find keys; DELETE in batches.
                deleted = 0
                async for k in r.scan_iter(match=f"{prefix}*", count=100):
                    await r.delete(k)
                    deleted += 1
                return deleted
            except Exception as exc:
                logger.warning("Redis scan/delete failed: %s; falling back", exc)

        async with self._lock:
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                del self._data[k]
            return len(keys)

    async def clear(self) -> None:
        r = await self._get_redis()
        if r is not None:
            try:
                await r.flushdb()
                return
            except Exception:
                pass

        async with self._lock:
            self._data.clear()

    async def stats(self) -> dict[str, Any]:
        async with self._lock:
            backend = "redis" if self._redis is not None else "memory"
            return {
                "backend": backend,
                "size": len(self._data) if backend == "memory" else None,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": (
                    self._hits / max(1, self._hits + self._misses)
                ),
            }

    async def cleanup_expired(self) -> int:
        """Eagerly evict expired entries (in-memory only; Redis does this itself)."""
        if self._redis is not None:
            return 0
        now = time.monotonic()
        async with self._lock:
            keys = [k for k, (_, exp) in self._data.items() if now > exp]
            for k in keys:
                del self._data[k]
            return len(keys)


# Singleton — shared across the app.
cache = TTLCache(default_ttl=60.0, max_entries=10_000)


# ----------------------------------------------------------- key helpers


def k_user_me(user_id: int) -> str:
    return f"me:{user_id}"


def k_user_quota(user_id: int) -> str:
    return f"quota:{user_id}"


def k_user_clouds(user_id: int, role: str) -> str:
    return f"clouds:{role}:{user_id}"


def k_files_in_cloud(cloud_id: int, user_id: int, role: str, limit: int, offset: int) -> str:
    return f"files:cloud={cloud_id}:{role}:{user_id}:l={limit}:o={offset}"


# ----------------------------------------------------------- invalidators


async def invalidate_user_quota(user_id: int) -> None:
    await cache.delete(k_user_quota(user_id))


async def invalidate_user_me(user_id: int) -> None:
    await cache.delete(k_user_me(user_id))


async def invalidate_user_clouds(user_id: int) -> None:
    await cache.delete(k_user_clouds(user_id, "user"))
    await cache.delete(k_user_clouds(user_id, "admin"))
    await cache.delete_prefix("clouds:admin:")


async def invalidate_files_in_cloud(cloud_id: int) -> None:
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
