"""Public sharing of files via opaque tokens.

Endpoints:
  POST   /api/v1/files/{id}/shares       (auth)  — mint a share link
  GET    /api/v1/files/{id}/shares       (auth)  — list owner's shares
  DELETE /api/v1/shares/{share_id}       (auth)  — revoke
  GET    /share/{token}                  (public, anonymous) — download

Properties:
  - Tokens are 32-byte url-safe random (43 chars base64url)
  - Optional expires_at and max_downloads enforced server-side
  - download_count tracked per share
  - Owner can list/revoke their own shares; cannot see others'
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Path, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from lcloud.auth.v2_deps import CurrentUser
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import Cloud, File, FileShare
from lcloud.metrics import share_downloads_counter
from lcloud.userbot.client import UserbotManager, get_userbot_manager
from lcloud.userbot.files import iter_download_file

logger = logging.getLogger(__name__)

# Owner-side router: nested under /files/{id}/shares + global /shares
shares_router = APIRouter(prefix="/api/v1", tags=["shares"])
# Public anonymous router: /share/{token}
public_share_router = APIRouter(prefix="/share", tags=["shares"], include_in_schema=True)


class CreateShareIn(BaseModel):
    expires_in_seconds: int | None = Field(
        default=None, ge=60, le=365 * 24 * 3600
    )
    max_downloads: int | None = Field(default=None, ge=1, le=10000)


def _serialize_share(s: FileShare, *, public_url: str | None = None) -> dict[str, Any]:
    out = {
        "id": s.id,
        "file_id": s.file_id,
        "token": s.token,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        "max_downloads": s.max_downloads,
        "download_count": s.download_count,
        "revoked_at": s.revoked_at.isoformat() if s.revoked_at else None,
        "active": s.revoked_at is None,
    }
    if public_url is not None:
        out["url"] = public_url
    return out


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _is_active(s: FileShare) -> bool:
    """Check if share is still usable (not revoked, not expired, has downloads left)."""
    if s.revoked_at is not None:
        return False
    if s.expires_at is not None:
        exp = s.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp < _now_utc():
            return False
    return not (
        s.max_downloads is not None and s.download_count >= s.max_downloads
    )


# ------------------------------------------------------------ create / list / revoke


@shares_router.post(
    "/files/{file_id}/shares",
    status_code=201,
    summary="Создать публичную ссылку на файл",
    description=(
        "Минтит токен для анонимного скачивания. Опции: время жизни в секундах "
        "(`expires_in_seconds`, мин 60, макс 1 год) и лимит скачиваний "
        "(`max_downloads`, до 10000). Без опций ссылка действует пока её не отозвут."
    ),
)
async def create_share(
    file_id: int,
    body: CreateShareIn,
    user: CurrentUser,
) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        f = (
            await sess.execute(
                sa.select(File).where(
                    File.id == file_id, File.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if f is None:
            raise HTTPException(404, detail={"reason": "not_found"})
        # Cross-user → 404 to avoid existence leak
        if user.role != "admin" and f.owner_user_id != user.id:
            raise HTTPException(404, detail={"reason": "not_found"})

        token = secrets.token_urlsafe(32)
        expires = (
            _now_utc() + timedelta(seconds=body.expires_in_seconds)
            if body.expires_in_seconds is not None
            else None
        )
        share = FileShare(
            file_id=f.id,
            owner_user_id=user.id,
            token=token,
            expires_at=expires,
            max_downloads=body.max_downloads,
        )
        sess.add(share)
        await sess.commit()
        await sess.refresh(share)

    from lcloud.config import get_settings

    base = get_settings().lc_public_base_url.rstrip("/")
    return _serialize_share(share, public_url=f"{base}/share/{token}")


@shares_router.get(
    "/files/{file_id}/shares",
    summary="Список ваших ссылок на файл",
)
async def list_shares_for_file(
    file_id: int, user: CurrentUser
) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        f = (
            await sess.execute(sa.select(File).where(File.id == file_id))
        ).scalar_one_or_none()
        if f is None or (
            user.role != "admin" and f.owner_user_id != user.id
        ):
            raise HTTPException(404, detail={"reason": "not_found"})
        rows = (
            await sess.execute(
                sa.select(FileShare)
                .where(FileShare.file_id == file_id)
                .order_by(FileShare.created_at.desc())
            )
        ).scalars().all()

    from lcloud.config import get_settings

    base = get_settings().lc_public_base_url.rstrip("/")
    return [
        _serialize_share(s, public_url=f"{base}/share/{s.token}") for s in rows
    ]


@shares_router.delete(
    "/shares/{share_id}",
    status_code=204,
    response_class=Response,
    summary="Отозвать ссылку",
)
async def revoke_share(share_id: int, user: CurrentUser) -> Response:
    sm = get_sessionmaker()
    async with sm() as sess:
        s = (
            await sess.execute(
                sa.select(FileShare).where(FileShare.id == share_id)
            )
        ).scalar_one_or_none()
        if s is None or (
            user.role != "admin" and s.owner_user_id != user.id
        ):
            raise HTTPException(404, detail={"reason": "not_found"})
        if s.revoked_at is None:
            s.revoked_at = _now_utc()
            await sess.commit()
    return Response(status_code=204)


# ------------------------------------------------------------ public download


@public_share_router.get(
    "/{token}",
    summary="Скачать файл по публичной ссылке",
    description=(
        "Анонимное скачивание. Сервер проверяет: не отозвана, не истекла "
        "по времени, не исчерпала лимит скачиваний. Каждое успешное "
        "скачивание увеличивает counter."
    ),
    responses={
        200: {"description": "Стрим файла"},
        404: {"description": "Ссылка не найдена / отозвана / истекла"},
    },
)
async def public_download(
    token: str = Path(min_length=20, max_length=64),
    manager: UserbotManager = Depends(get_userbot_manager),
) -> StreamingResponse:
    sm = get_sessionmaker()
    async with sm() as sess:
        share = (
            await sess.execute(
                sa.select(FileShare).where(FileShare.token == token)
            )
        ).scalar_one_or_none()
        if share is None or not _is_active(share):
            raise HTTPException(404, detail={"reason": "not_found"})

        f = (
            await sess.execute(
                sa.select(File).where(
                    File.id == share.file_id, File.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if f is None:
            raise HTTPException(404, detail={"reason": "not_found"})
        cloud = (
            await sess.execute(sa.select(Cloud).where(Cloud.id == f.cloud_id))
        ).scalar_one()

        # Bump counter
        share.download_count = (share.download_count or 0) + 1
        await sess.commit()

    if not manager.is_started or not await manager.is_admin_authorized():
        raise HTTPException(503, detail={"reason": "userbot_not_authorized"})

    share_downloads_counter.inc()

    headers = {
        "Content-Disposition": f'attachment; filename="{f.original_name}"',
        "Content-Length": str(f.size_bytes),
    }

    async def _gen() -> Any:
        async for chunk in iter_download_file(
            manager.client, chat_id=cloud.chat_id, message_id=f.message_id
        ):
            yield chunk

    return StreamingResponse(
        _gen(),
        media_type=f.mime or "application/octet-stream",
        headers=headers,
    )


__all__ = ["public_share_router", "shares_router"]
