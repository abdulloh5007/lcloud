"""Per-user storage quota tracking & enforcement.

`users.storage_used_bytes` is the running total of all live files owned by a
user. Updated atomically:
- INCREMENT on successful upload (after TG ack + DB commit)
- DECREMENT on hard delete (or restore-on-error semantics during upload)

Quota check is done **before** writing to TG to avoid uploading a file we
can't legally store.

Note: This is a soft accounting layer; the underlying TG account has no
per-user limits. If the count drifts (e.g. crash between TG upload and DB
commit), an admin tool can recompute it from `SUM(files.size_bytes)`.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker

from lcloud.db.base import get_sessionmaker
from lcloud.db.models import File, User

logger = logging.getLogger(__name__)


async def get_used_and_quota(user_id: int) -> tuple[int, int]:
    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(User.storage_used_bytes, User.storage_quota_bytes).where(
                    User.id == user_id
                )
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(404, detail={"reason": "user_missing"})
    return int(row[0]), int(row[1])


async def assert_can_store(user_id: int, additional_bytes: int) -> None:
    """Raise HTTP 413 if `additional_bytes` would push the user over quota."""
    if additional_bytes < 0:
        raise ValueError("additional_bytes must be >= 0")
    used, quota = await get_used_and_quota(user_id)
    if used + additional_bytes > quota:
        raise HTTPException(
            413,
            detail={
                "reason": "quota_exceeded",
                "used": used,
                "quota": quota,
                "would_add": additional_bytes,
            },
        )


async def increment_used(
    user_id: int, delta_bytes: int, *, sessionmaker: async_sessionmaker[Any] | None = None
) -> int:
    """Atomically add `delta_bytes` to user's used. Returns new total.

    Pass `sessionmaker` from inside an outer transaction; otherwise we
    open our own short-lived session.
    """
    if delta_bytes == 0:
        used, _ = await get_used_and_quota(user_id)
        return used

    sm = sessionmaker or get_sessionmaker()
    async with sm() as sess:
        await sess.execute(
            sa.update(User)
            .where(User.id == user_id)
            .values(storage_used_bytes=User.storage_used_bytes + delta_bytes)
        )
        await sess.commit()
        new_total = (
            await sess.execute(
                sa.select(User.storage_used_bytes).where(User.id == user_id)
            )
        ).scalar_one()
    return int(new_total)


async def recompute_used(user_id: int) -> int:
    """Admin recovery: rebuild `storage_used_bytes` by summing live files."""
    sm = get_sessionmaker()
    async with sm() as sess:
        total = (
            await sess.execute(
                sa.select(sa.func.coalesce(sa.func.sum(File.size_bytes), 0)).where(
                    File.owner_user_id == user_id, File.deleted_at.is_(None)
                )
            )
        ).scalar_one()
        await sess.execute(
            sa.update(User)
            .where(User.id == user_id)
            .values(storage_used_bytes=int(total))
        )
        await sess.commit()
    return int(total)


__all__ = [
    "assert_can_store",
    "get_used_and_quota",
    "increment_used",
    "recompute_used",
]
