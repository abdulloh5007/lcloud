"""V2 API key management: mint / list / revoke.

All endpoints require the caller to be an authenticated user (cookie or
Bearer-with-existing-key). The minted raw key is shown **exactly once**.

Endpoints:
    POST   /api/v1/keys              — mint a new key (returns raw + meta)
    GET    /api/v1/keys              — list current user's keys (no raw)
    DELETE /api/v1/keys/{key_id}     — revoke a key
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lcloud.auth import api_keys as ak
from lcloud.auth.v2_deps import CurrentUser
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import ApiKey

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/keys", tags=["api_keys"])

MAX_KEYS_PER_USER = 25


class MintIn(BaseModel):
    label: str = Field("", max_length=64)


class KeyOut(BaseModel):
    id: int
    prefix: str
    label: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None


class MintOut(KeyOut):
    raw: str = Field(description="Full API key. Shown ONCE — store it now.")


def _serialize(row: ApiKey, *, raw: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "prefix": row.prefix,
        "label": row.label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }
    if raw is not None:
        out["raw"] = raw
    return out


@router.post("", response_model=MintOut)
async def mint(body: MintIn, user: CurrentUser) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        # Rate-limit by total active keys to avoid runaway minting
        active = (
            await sess.execute(
                sa.select(sa.func.count())
                .select_from(ApiKey)
                .where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
            )
        ).scalar_one()
        if active >= MAX_KEYS_PER_USER:
            raise HTTPException(
                400,
                detail={
                    "reason": "key_limit_reached",
                    "max": MAX_KEYS_PER_USER,
                },
            )

        minted = ak.mint_key()
        row = ApiKey(
            user_id=user.id,
            hash=minted.hash,
            prefix=minted.prefix,
            label=body.label.strip(),
        )
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)
        logger.info(
            "minted api key id=%d user_id=%d prefix=%s",
            row.id,
            user.id,
            minted.prefix,
        )
        return _serialize(row, raw=minted.raw)


@router.get("", response_model=list[KeyOut])
async def list_keys(user: CurrentUser) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        rows = (
            await sess.execute(
                sa.select(ApiKey)
                .where(ApiKey.user_id == user.id)
                .order_by(ApiKey.created_at.desc())
            )
        ).scalars().all()
        return [_serialize(r) for r in rows]


@router.delete("/{key_id}")
async def revoke(key_id: int, user: CurrentUser) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(ApiKey).where(
                    ApiKey.id == key_id, ApiKey.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "not_found"})
        if row.revoked_at is not None:
            return {"ok": True, "already_revoked": True}
        row.revoked_at = datetime.now(UTC)
        await sess.commit()
        logger.info("revoked api key id=%d user_id=%d", key_id, user.id)
        return {"ok": True}


__all__ = ["router"]
