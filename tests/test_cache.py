"""Tests for the in-process RAM cache."""

from __future__ import annotations

import asyncio

import pytest

from lcloud.cache import TTLCache


@pytest.mark.asyncio
async def test_ttl_cache_hit_miss_and_expiry() -> None:
    cache = TTLCache(default_ttl=0.05, max_entries=10, max_bytes=10_000)

    assert await cache.get("json_doc:1:a") is None
    await cache.set("json_doc:1:a", {"id": "a"}, namespace="json_doc")
    assert await cache.get("json_doc:1:a") == {"id": "a"}

    await asyncio.sleep(0.06)
    assert await cache.get("json_doc:1:a") is None

    stats = await cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["expired"] == 1


@pytest.mark.asyncio
async def test_ttl_cache_lru_eviction_and_prefix_delete() -> None:
    cache = TTLCache(default_ttl=60, max_entries=2, max_bytes=10_000)

    await cache.set("json_doc:1:a", {"id": "a"}, namespace="json_doc")
    await cache.set("json_doc:1:b", {"id": "b"}, namespace="json_doc")
    assert await cache.get("json_doc:1:a") == {"id": "a"}
    await cache.set("json_doc:1:c", {"id": "c"}, namespace="json_doc")

    assert await cache.get("json_doc:1:b") is None
    assert await cache.get("json_doc:1:a") == {"id": "a"}
    assert await cache.delete_prefix("json_doc:1:") == 2
    assert await cache.get("json_doc:1:a") is None
