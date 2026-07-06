"""In-process TTL/LRU cache for read-heavy LCloud endpoints.

The database and Telegram remain the source of truth. This cache only stores
derived read responses and authorization lookups so repeated public/API reads do
not hit SQLite for every request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid integer for %s=%r; using %s", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid float for %s=%r; using %s", name, raw, default)
        return default


@dataclass
class _Entry:
    value: Any
    expires_at: float
    size_bytes: int
    namespace: str


class TTLCache:
    """Async-safe in-memory TTL + LRU cache.

    `get()` returns None for misses, expired entries, and disabled cache. Store
    explicit sentinel objects if a caller needs to cache None-like values.
    """

    def __init__(
        self,
        *,
        default_ttl: float = 60.0,
        max_entries: int = 50_000,
        max_bytes: int = 128 * 1024 * 1024,
        enabled: bool = True,
    ) -> None:
        self._data: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._enabled = enabled
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._deletes = 0
        self._evictions = 0
        self._expired = 0
        self._namespace_hits: dict[str, int] = defaultdict(int)
        self._namespace_misses: dict[str, int] = defaultdict(int)
        self._namespace_entries: dict[str, int] = defaultdict(int)
        self._namespace_bytes: dict[str, int] = defaultdict(int)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def configure(
        self,
        *,
        enabled: bool | None = None,
        default_ttl: float | None = None,
        max_entries: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        if enabled is not None:
            self._enabled = enabled
        if default_ttl is not None:
            self._default_ttl = default_ttl
        if max_entries is not None:
            self._max_entries = max_entries
        if max_bytes is not None:
            self._max_bytes = max_bytes

    async def get(self, key: str) -> Any | None:
        if not self._enabled:
            self._misses += 1
            return None
        now = time.monotonic()
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self._misses += 1
                self._namespace_misses[_namespace(key)] += 1
                return None
            if now > entry.expires_at:
                self._remove_locked(key, reason="expired")
                self._misses += 1
                self._namespace_misses[entry.namespace] += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            self._namespace_hits[entry.namespace] += 1
            return entry.value

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ttl: float | None = None,
        namespace: str | None = None,
    ) -> None:
        if not self._enabled:
            return
        ttl_v = self._default_ttl if ttl is None else ttl
        if ttl_v <= 0:
            return
        ns = namespace or _namespace(key)
        size = _estimate_size(value) + len(key)
        expires_at = time.monotonic() + ttl_v
        async with self._lock:
            old = self._data.pop(key, None)
            if old is not None:
                self._account_remove(old)
            entry = _Entry(
                value=value,
                expires_at=expires_at,
                size_bytes=size,
                namespace=ns,
            )
            self._data[key] = entry
            self._account_add(entry)
            self._sets += 1
            self._evict_locked()

    async def delete(self, key: str) -> None:
        async with self._lock:
            if self._remove_locked(key, reason="delete"):
                self._deletes += 1

    async def delete_prefix(self, prefix: str) -> int:
        async with self._lock:
            keys = [key for key in self._data if key.startswith(prefix)]
            for key in keys:
                self._remove_locked(key, reason="delete")
            self._deletes += len(keys)
            return len(keys)

    async def delete_namespace(self, namespace: str) -> int:
        async with self._lock:
            keys = [
                key
                for key, entry in self._data.items()
                if entry.namespace == namespace
            ]
            for key in keys:
                self._remove_locked(key, reason="delete")
            self._deletes += len(keys)
            return len(keys)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()
            self._bytes = 0
            self._namespace_entries.clear()
            self._namespace_bytes.clear()

    async def stats(self) -> dict[str, Any]:
        async with self._lock:
            total = self._hits + self._misses
            namespaces = sorted(
                set(self._namespace_entries)
                | set(self._namespace_hits)
                | set(self._namespace_misses)
            )
            return {
                "backend": "memory",
                "enabled": self._enabled,
                "entries": len(self._data),
                "max_entries": self._max_entries,
                "bytes": self._bytes,
                "max_bytes": self._max_bytes,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / total if total else 0.0,
                "sets": self._sets,
                "deletes": self._deletes,
                "evictions": self._evictions,
                "expired": self._expired,
                "namespaces": {
                    namespace: {
                        "entries": self._namespace_entries.get(namespace, 0),
                        "bytes": self._namespace_bytes.get(namespace, 0),
                        "hits": self._namespace_hits.get(namespace, 0),
                        "misses": self._namespace_misses.get(namespace, 0),
                    }
                    for namespace in namespaces
                },
            }

    async def cleanup_expired(self) -> int:
        now = time.monotonic()
        async with self._lock:
            keys = [
                key
                for key, entry in self._data.items()
                if now > entry.expires_at
            ]
            for key in keys:
                self._remove_locked(key, reason="expired")
            return len(keys)

    def _evict_locked(self) -> None:
        while self._data and (
            len(self._data) > self._max_entries or self._bytes > self._max_bytes
        ):
            key, _entry = next(iter(self._data.items()))
            self._remove_locked(key, reason="evict")

    def _remove_locked(self, key: str, *, reason: str) -> bool:
        entry = self._data.pop(key, None)
        if entry is None:
            return False
        self._account_remove(entry)
        if reason == "evict":
            self._evictions += 1
        elif reason == "expired":
            self._expired += 1
        return True

    def _account_add(self, entry: _Entry) -> None:
        self._bytes += entry.size_bytes
        self._namespace_entries[entry.namespace] += 1
        self._namespace_bytes[entry.namespace] += entry.size_bytes

    def _account_remove(self, entry: _Entry) -> None:
        self._bytes = max(0, self._bytes - entry.size_bytes)
        self._namespace_entries[entry.namespace] = max(
            0, self._namespace_entries[entry.namespace] - 1
        )
        self._namespace_bytes[entry.namespace] = max(
            0, self._namespace_bytes[entry.namespace] - entry.size_bytes
        )


def _namespace(key: str) -> str:
    return key.split(":", 1)[0] if ":" in key else "default"


def _estimate_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return len(repr(value).encode("utf-8"))


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()[:24]


cache = TTLCache(
    default_ttl=_env_float("LC_CACHE_DEFAULT_TTL_SECONDS", 30.0),
    max_entries=_env_int("LC_CACHE_MAX_ENTRIES", 50_000),
    max_bytes=_env_int("LC_CACHE_MAX_BYTES", 128 * 1024 * 1024),
    enabled=_env_bool("LC_CACHE_ENABLED", True),
)


JSON_DB_TTL = _env_float("LC_CACHE_JSON_DB_TTL_SECONDS", 300.0)
JSON_COLLECTION_TTL = _env_float("LC_CACHE_JSON_COLLECTION_TTL_SECONDS", 120.0)
JSON_DOCUMENT_TTL = _env_float("LC_CACHE_JSON_DOCUMENT_TTL_SECONDS", 30.0)
JSON_QUERY_TTL = _env_float("LC_CACHE_JSON_QUERY_TTL_SECONDS", 10.0)
JSON_META_TTL = _env_float("LC_CACHE_JSON_META_TTL_SECONDS", 300.0)
PUBLIC_KEY_TTL = _env_float("LC_CACHE_PUBLIC_KEY_TTL_SECONDS", 300.0)


# ----------------------------------------------------------- key helpers


def k_user_me(user_id: int) -> str:
    return f"me:{user_id}"


def k_user_quota(user_id: int) -> str:
    return f"quota:{user_id}"


def k_user_clouds(user_id: int, role: str) -> str:
    return f"clouds:{role}:{user_id}"


def k_files_in_cloud(
    cloud_id: int, user_id: int, role: str, limit: int, offset: int
) -> str:
    return f"files:cloud={cloud_id}:{role}:{user_id}:l={limit}:o={offset}"


def k_json_meta() -> str:
    return "json_meta:v1"


def k_json_database_key(database_key: str) -> str:
    return f"json_database:key:{database_key}"


def k_json_public_key(public_key: str) -> str:
    return f"json_public_key:{public_key}"


def k_json_storage_key(storage_key: str) -> str:
    return f"json_storage_key:{storage_key}"


def k_json_collection(collection_id: int) -> str:
    return f"json_collection:id:{collection_id}"


def k_json_collection_name(
    *, owner_user_id: int, database_id: int | str, name: str
) -> str:
    return f"json_collection:name:{owner_user_id}:{database_id}:{name}"


def k_json_document(collection_id: int, doc_id: str) -> str:
    return f"json_doc:{collection_id}:{doc_id}"


def k_json_list(collection_id: int, limit: int, offset: int) -> str:
    return f"json_list:{collection_id}:{limit}:{offset}"


def k_json_query(collection_id: int, query: Any) -> str:
    return f"json_query:{collection_id}:{stable_hash(query)}"


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


async def invalidate_json_database(database_id: int | None = None) -> None:
    await cache.delete_namespace("json_meta")
    if database_id is None:
        await cache.delete_namespace("json_database")
        await cache.delete_namespace("json_collection")
        return
    await cache.delete_prefix("json_collection:name:")


async def invalidate_json_public_keys() -> None:
    await cache.delete_namespace("json_public_key")


async def invalidate_json_storage_keys() -> None:
    await cache.delete_namespace("json_storage_key")


async def invalidate_json_collection(collection_id: int) -> None:
    await cache.delete(k_json_collection(collection_id))
    await cache.delete_prefix(f"json_doc:{collection_id}:")
    await cache.delete_prefix(f"json_list:{collection_id}:")
    await cache.delete_prefix(f"json_query:{collection_id}:")


async def invalidate_json_document(collection_id: int, doc_id: str) -> None:
    await cache.delete(k_json_document(collection_id, doc_id))
    await cache.delete_prefix(f"json_list:{collection_id}:")
    await cache.delete_prefix(f"json_query:{collection_id}:")


__all__ = [
    "JSON_COLLECTION_TTL",
    "JSON_DB_TTL",
    "JSON_DOCUMENT_TTL",
    "JSON_META_TTL",
    "JSON_QUERY_TTL",
    "PUBLIC_KEY_TTL",
    "TTLCache",
    "cache",
    "invalidate_files_in_cloud",
    "invalidate_json_collection",
    "invalidate_json_database",
    "invalidate_json_document",
    "invalidate_json_public_keys",
    "invalidate_json_storage_keys",
    "invalidate_user_clouds",
    "invalidate_user_me",
    "invalidate_user_quota",
    "k_files_in_cloud",
    "k_json_collection",
    "k_json_collection_name",
    "k_json_database_key",
    "k_json_document",
    "k_json_list",
    "k_json_meta",
    "k_json_public_key",
    "k_json_query",
    "k_json_storage_key",
    "k_user_clouds",
    "k_user_me",
    "k_user_quota",
    "stable_hash",
]
