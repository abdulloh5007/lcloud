"""File versioning + listing previous versions.

When a user uploads a file with the same `original_name` to the same
cloud, we mark the previous live row as superseded (deleted_at = now,
replaces_file_id of the new row points back to it).

Endpoint here: GET /api/v1/files/{id}/versions — returns the chain of
this file's history (newer rows in this file's "lineage").
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException

from lcloud.auth.v2_deps import CurrentUser
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import File

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["v2_files"])


def _serialize(f: File) -> dict[str, Any]:
    return {
        "id": f.id,
        "original_name": f.original_name,
        "size_bytes": f.size_bytes,
        "mime": f.mime,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
        "deleted_at": f.deleted_at.isoformat() if f.deleted_at else None,
        "replaces_file_id": f.replaces_file_id,
        "compressed": bool(getattr(f, "compressed", False)),
    }


@router.get(
    "/files/{file_id}/versions",
    summary="История версий файла",
    description=(
        "Возвращает все предыдущие версии этого файла "
        "(когда вы загрузили файл с тем же именем в тот же cloud, "
        "старая версия становится 'superseded'). "
        "Самая свежая версия — первая в списке."
    ),
)
async def list_versions(
    file_id: int, user: CurrentUser
) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        # Permission check on the requested file
        anchor = (
            await sess.execute(
                sa.select(File).where(File.id == file_id)
            )
        ).scalar_one_or_none()
        if anchor is None or (
            user.role != "admin" and anchor.owner_user_id != user.id
        ):
            raise HTTPException(404, detail={"reason": "not_found"})

        # Walk the replaces-chain backwards: find this file + all rows with
        # the same name+cloud that were superseded.
        chain: list[File] = [anchor]
        cursor = anchor
        # follow replaces_file_id back
        while cursor.replaces_file_id is not None:
            prev = (
                await sess.execute(
                    sa.select(File).where(File.id == cursor.replaces_file_id)
                )
            ).scalar_one_or_none()
            if prev is None:
                break
            chain.append(prev)
            cursor = prev

        # Forward direction: any row that has this id as replaces_file_id
        forward = (
            await sess.execute(
                sa.select(File).where(File.replaces_file_id == file_id)
            )
        ).scalars().all()
        for fwd in forward:
            if fwd not in chain:
                chain.insert(0, fwd)

    return [_serialize(f) for f in chain]


__all__ = ["router"]
