"""Search router: name-FTS5 + tag intersection over a single owner's files."""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query

from lcloud.auth.deps import require_admin
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import Cloud, File, FileTag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


def _serialize_file(f: File) -> dict[str, Any]:
    return {
        "id": f.id,
        "cloud_id": f.cloud_id,
        "message_id": f.message_id,
        "name": f.original_name,
        "mime": f.mime,
        "size": f.size_bytes,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
    }


def _fts_quote(query: str) -> str:
    """Wrap each term in double-quotes so FTS5 does literal matching, then
    join with AND. Strips quotes inside the input to avoid syntax breakage."""
    cleaned = query.replace('"', " ").strip()
    if not cleaned:
        return ""
    parts = [p for p in cleaned.split() if p]
    if not parts:
        return ""
    return " AND ".join(f'"{p}"*' for p in parts)


@router.get("")
async def search(
    q: str | None = Query(default=None, max_length=200),
    cloud_id: int | None = Query(default=None, ge=1),
    tag: list[int] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    owner_id: int = Depends(require_admin),
) -> dict[str, Any]:
    """List files that match name (FTS5) AND are in `cloud_id` (if given)
    AND have ALL `tag` ids (intersection). Soft-deleted files excluded."""
    sm = get_sessionmaker()
    tag_ids = sorted(set(tag or []))

    # Build base SELECT on files joined to clouds for owner-scoping
    stmt = (
        sa.select(File)
        .join(Cloud, Cloud.id == File.cloud_id)
        .where(File.owner_id == owner_id, File.deleted_at.is_(None))
    )
    if cloud_id is not None:
        stmt = stmt.where(File.cloud_id == cloud_id)

    # Tag intersection: file must have AT LEAST one row in file_tags for
    # EACH requested tag_id. Done with HAVING COUNT(DISTINCT tag) == N.
    if tag_ids:
        stmt = (
            stmt.join(FileTag, FileTag.file_id == File.id)
            .where(FileTag.tag_id.in_(tag_ids))
            .group_by(File.id)
            .having(sa.func.count(sa.distinct(FileTag.tag_id)) == len(tag_ids))
        )

    # FTS5 filter via subquery on rowids matching the query string
    if q:
        match = _fts_quote(q)
        if match:
            fts_sub = sa.text(
                "SELECT rowid FROM files_fts WHERE files_fts MATCH :match"
            ).bindparams(match=match)
            stmt = stmt.where(File.id.in_(fts_sub))

    stmt = stmt.order_by(File.uploaded_at.desc(), File.id.desc())

    # Build a paired count query reusing the same WHERE/JOIN structure
    count_stmt = sa.select(sa.func.count()).select_from(stmt.subquery())

    paginated = stmt.limit(limit).offset(offset)

    async with sm() as sess:
        total = (await sess.execute(count_stmt)).scalar_one()
        rows = (await sess.execute(paginated)).scalars().all()
    return {
        "items": [_serialize_file(f) for f in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }
