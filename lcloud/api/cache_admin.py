"""Owner-only cache inspection and control endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from lcloud.auth.v2_deps import CurrentUser
from lcloud.cache import cache

router = APIRouter(prefix="/api/v1/cache", tags=["cache"])


@router.get("/stats")
async def cache_stats(_user: CurrentUser) -> dict[str, Any]:
    return await cache.stats()


@router.post("/clear")
async def cache_clear(_user: CurrentUser) -> dict[str, Any]:
    await cache.clear()
    return {"ok": True}


__all__ = ["router"]
