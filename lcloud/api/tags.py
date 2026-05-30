"""Tags router: CRUD for tags + per-file tag assignment."""

from __future__ import annotations

import logging
import re
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from lcloud.auth.deps import require_admin
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import File, FileTag, Tag

logger = logging.getLogger(__name__)
tags_router = APIRouter(prefix="/tags", tags=["tags"])
file_tags_router = APIRouter(prefix="/files/{file_id}/tags", tags=["tags"])

# Permissive HEX colour (#rgb / #rrggbb / #rrggbbaa) or named CSS colour token.
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$|^[a-zA-Z]+$")


def _validate_color(v: str) -> str:
    if not _COLOR_RE.match(v):
        raise ValueError("color must be #hex or a CSS named colour")
    return v


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    color: str = Field(min_length=1, max_length=32)
    icon: str = Field(min_length=1, max_length=64)
    bg_color: str = Field(min_length=1, max_length=32)

    @field_validator("color", "bg_color")
    @classmethod
    def _color_ok(cls, v: str) -> str:
        return _validate_color(v)


class TagPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = Field(default=None, min_length=1, max_length=32)
    icon: str | None = Field(default=None, min_length=1, max_length=64)
    bg_color: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("color", "bg_color")
    @classmethod
    def _color_ok(cls, v: str | None) -> str | None:
        return _validate_color(v) if v is not None else v


class FileTagsIn(BaseModel):
    tag_ids: list[int] = Field(default_factory=list)


def _serialize_tag(t: Tag) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "color": t.color,
        "icon": t.icon,
        "bg_color": t.bg_color,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


# ------------------------------------------------------------------ /tags


@tags_router.get("")
async def list_tags(
    owner_id: int = Depends(require_admin),
) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(Tag).where(Tag.owner_id == owner_id).order_by(Tag.name)
        )
        rows = result.scalars().all()
    return [_serialize_tag(t) for t in rows]


@tags_router.post("", status_code=201)
async def create_tag(
    body: TagIn,
    owner_id: int = Depends(require_admin),
) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        existing = await sess.execute(
            sa.select(Tag).where(Tag.owner_id == owner_id, Tag.name == body.name)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(409, detail={"reason": "tag_name_exists"})
        tag = Tag(
            owner_id=owner_id,
            name=body.name,
            color=body.color,
            icon=body.icon,
            bg_color=body.bg_color,
        )
        sess.add(tag)
        await sess.commit()
        await sess.refresh(tag)
    return _serialize_tag(tag)


@tags_router.patch("/{tag_id}")
async def update_tag(
    tag_id: int,
    body: TagPatch,
    owner_id: int = Depends(require_admin),
) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(Tag).where(Tag.id == tag_id, Tag.owner_id == owner_id)
        )
        tag = result.scalar_one_or_none()
        if tag is None:
            raise HTTPException(404, detail={"reason": "tag_not_found"})
        if body.name is not None and body.name != tag.name:
            # Check for name conflict
            conflict = await sess.execute(
                sa.select(Tag).where(
                    Tag.owner_id == owner_id,
                    Tag.name == body.name,
                    Tag.id != tag_id,
                )
            )
            if conflict.scalar_one_or_none() is not None:
                raise HTTPException(409, detail={"reason": "tag_name_exists"})
            tag.name = body.name
        if body.color is not None:
            tag.color = body.color
        if body.icon is not None:
            tag.icon = body.icon
        if body.bg_color is not None:
            tag.bg_color = body.bg_color
        await sess.commit()
        await sess.refresh(tag)
    return _serialize_tag(tag)


@tags_router.delete("/{tag_id}", status_code=204, response_class=Response)
async def delete_tag(
    tag_id: int,
    owner_id: int = Depends(require_admin),
) -> Response:
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(Tag).where(Tag.id == tag_id, Tag.owner_id == owner_id)
        )
        tag = result.scalar_one_or_none()
        if tag is None:
            raise HTTPException(404, detail={"reason": "tag_not_found"})
        await sess.delete(tag)  # ON DELETE CASCADE drops file_tags rows
        await sess.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ /files/{id}/tags


@file_tags_router.get("")
async def get_file_tags(
    file_id: int,
    owner_id: int = Depends(require_admin),
) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        # Check file belongs to owner
        f = (
            await sess.execute(
                sa.select(File).where(
                    File.id == file_id,
                    File.owner_id == owner_id,
                    File.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if f is None:
            raise HTTPException(404, detail={"reason": "file_not_found"})
        # Join tags via file_tags
        result = await sess.execute(
            sa.select(Tag)
            .join(FileTag, FileTag.tag_id == Tag.id)
            .where(FileTag.file_id == file_id, Tag.owner_id == owner_id)
            .order_by(Tag.name)
        )
        rows = result.scalars().all()
    return [_serialize_tag(t) for t in rows]


@file_tags_router.put("")
async def set_file_tags(
    file_id: int,
    body: FileTagsIn,
    owner_id: int = Depends(require_admin),
) -> dict[str, Any]:
    """Replace the tag set for a file with `body.tag_ids`. All ids must be
    tags owned by the caller; unknown ids → 404."""
    sm = get_sessionmaker()
    requested = sorted(set(body.tag_ids))
    async with sm() as sess:
        f = (
            await sess.execute(
                sa.select(File).where(
                    File.id == file_id,
                    File.owner_id == owner_id,
                    File.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if f is None:
            raise HTTPException(404, detail={"reason": "file_not_found"})

        if requested:
            owned = (
                await sess.execute(
                    sa.select(Tag.id).where(
                        Tag.owner_id == owner_id, Tag.id.in_(requested)
                    )
                )
            ).scalars().all()
            if set(owned) != set(requested):
                raise HTTPException(
                    404,
                    detail={
                        "reason": "unknown_tag_ids",
                        "missing": sorted(set(requested) - set(owned)),
                    },
                )

        # Diff vs current
        current = (
            await sess.execute(
                sa.select(FileTag.tag_id).where(FileTag.file_id == file_id)
            )
        ).scalars().all()
        current_set = set(current)
        target_set = set(requested)
        to_add = target_set - current_set
        to_remove = current_set - target_set

        for tid in to_remove:
            await sess.execute(
                sa.delete(FileTag).where(
                    FileTag.file_id == file_id, FileTag.tag_id == tid
                )
            )
        for tid in to_add:
            sess.add(FileTag(file_id=file_id, tag_id=tid))
        await sess.commit()

    return {"file_id": file_id, "tag_ids": sorted(target_set)}
