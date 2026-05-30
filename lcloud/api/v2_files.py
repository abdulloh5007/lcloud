"""V2 per-user files endpoints (under /api/v1/files and /api/v1/clouds/{id}/files).

Per-user scoping via `files.owner_user_id`. TG-side operations still use
the bootstrap admin's account (single Telethon session) but logical
ownership and quota accounting belong to the calling V2 user.

Round 5a scope: list / upload / download / delete + quota tracking.
Future (Round 5b): client-side LC2 caption signing on upload, rename,
thumbnails, search, tags.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi import (
    File as FileParam,
)
from fastapi.responses import StreamingResponse

from lcloud.auth.storage_quota import (
    assert_can_store,
    get_used_and_quota,
    increment_used,
)
from lcloud.auth.v2_deps import CurrentUser
from lcloud.config import get_settings
from lcloud.crypto.keys import ensure_admin_keypair
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import Cloud, File
from lcloud.userbot.client import UserbotManager, get_userbot_manager
from lcloud.userbot.files import (
    UploadResult,
    delete_file_message,
    iter_download_file,
    upload_file_to_cloud,
)
from lcloud.workers import get_worker_pool

logger = logging.getLogger(__name__)

clouds_files_router = APIRouter(
    prefix="/api/v1/clouds/{cloud_id}/files", tags=["v2_files"]
)
files_router = APIRouter(prefix="/api/v1/files", tags=["v2_files"])


def _serialize(f: File) -> dict[str, Any]:
    return {
        "id": f.id,
        "cloud_id": f.cloud_id,
        "message_id": f.message_id,
        "owner_user_id": f.owner_user_id,
        "name": f.original_name,
        "mime": f.mime,
        "size": f.size_bytes,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
        "deleted_at": f.deleted_at.isoformat() if f.deleted_at else None,
    }


async def _ensure_userbot_authorized(manager: UserbotManager) -> None:
    if not manager.is_started:
        raise HTTPException(503, detail={"reason": "userbot_not_started"})
    if not await manager.is_admin_authorized():
        raise HTTPException(409, detail={"reason": "userbot_not_authorized"})


async def _get_cloud_for_user(cloud_id: int, user_id: int, *, role: str) -> Cloud:
    sm = get_sessionmaker()
    async with sm() as sess:
        cloud = (
            await sess.execute(sa.select(Cloud).where(Cloud.id == cloud_id))
        ).scalar_one_or_none()
    if cloud is None:
        raise HTTPException(404, detail={"reason": "cloud_not_found"})
    # Authorization: admins see all; users only their own clouds
    if role != "admin" and cloud.owner_user_id != user_id:
        raise HTTPException(403, detail={"reason": "forbidden"})
    return cloud


async def _stream_to_temp(
    upload: UploadFile, tmp_dir: Path, max_size: int
) -> tuple[Path, int, bytes]:
    tmp_path = tmp_dir / f"upload-{uuid.uuid4().hex}.bin"
    h = hashlib.sha256()
    size = 0
    chunk_size = 1024 * 1024
    try:
        with tmp_path.open("wb") as out:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(
                        413,
                        detail={
                            "reason": "file_too_large",
                            "size": size,
                            "limit": max_size,
                        },
                    )
                h.update(chunk)
                out.write(chunk)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise
    return tmp_path, size, h.digest()


# ------------------------------------------------------------------ list


@clouds_files_router.get("")
async def list_files(
    cloud_id: int,
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    cloud = await _get_cloud_for_user(cloud_id, user.id, role=user.role)
    sm = get_sessionmaker()
    async with sm() as sess:
        cond = sa.and_(File.cloud_id == cloud.id, File.deleted_at.is_(None))
        if user.role != "admin":
            cond = sa.and_(cond, File.owner_user_id == user.id)
        total = (
            await sess.execute(
                sa.select(sa.func.count()).select_from(File).where(cond)
            )
        ).scalar_one()
        rows = (
            await sess.execute(
                sa.select(File)
                .where(cond)
                .order_by(File.uploaded_at.desc(), File.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    return {
        "items": [_serialize(f) for f in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


# ------------------------------------------------------------------ upload


@clouds_files_router.post("", status_code=201)
async def upload_file(
    cloud_id: int,
    user: CurrentUser,
    file: UploadFile = FileParam(...),
    manager: UserbotManager = Depends(get_userbot_manager),
) -> dict[str, Any]:
    """Upload a file to the given cloud. Increments user's storage_used_bytes
    on success; rejects with 413 if over quota. Pre-flight check uses the
    HTTP Content-Length when available; final check is at temp-file size."""
    await _ensure_userbot_authorized(manager)
    cloud = await _get_cloud_for_user(cloud_id, user.id, role=user.role)
    settings = get_settings()
    sk, _ = ensure_admin_keypair(settings)
    pool = get_worker_pool()

    # Stream to disk first so we know the real size for quota check
    tmp_path, size, sha = await _stream_to_temp(
        file, settings.data_dir / "tmp", settings.lc_max_file_bytes
    )

    # Quota check (HTTP 413 if would exceed)
    try:
        await assert_can_store(user.id, size)
    except HTTPException:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise

    try:
        result: UploadResult = await pool.submit(
            upload_file_to_cloud(
                manager.client,
                chat_id=cloud.chat_id,
                file_path=tmp_path,
                original_name=file.filename or f"file-{uuid.uuid4().hex}",
                sha256_digest=sha,
                signing_key=sk,
            )
        )
    except Exception as exc:
        logger.exception("upload to Telegram failed: %s", exc)
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise HTTPException(
            502, detail={"reason": "telegram_upload_failed", "error": str(exc)}
        ) from None
    else:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()

    # Persist file row + bump quota in one transaction-ish block
    sm = get_sessionmaker()
    async with sm() as sess:
        row = File(
            cloud_id=cloud.id,
            message_id=result.message_id,
            owner_id=cloud.owner_id,  # admin (TG-side signer)
            owner_user_id=user.id,  # logical owner
            original_name=file.filename or f"file-{uuid.uuid4().hex}",
            mime=file.content_type or "application/octet-stream",
            size_bytes=size,
            sha256=sha,
            signature=result.signature,
        )
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)

    await increment_used(user.id, size)
    return _serialize(row)


# ------------------------------------------------------------------ download


@files_router.get("/{file_id}/download")
async def download_file(
    file_id: int,
    user: CurrentUser,
    manager: UserbotManager = Depends(get_userbot_manager),
) -> StreamingResponse:
    await _ensure_userbot_authorized(manager)
    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(File).where(
                    File.id == file_id, File.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "file_not_found"})
        if user.role != "admin" and row.owner_user_id != user.id:
            raise HTTPException(403, detail={"reason": "forbidden"})
        cloud = (
            await sess.execute(sa.select(Cloud).where(Cloud.id == row.cloud_id))
        ).scalar_one()

    headers = {
        "Content-Disposition": f'attachment; filename="{row.original_name}"',
        "Content-Length": str(row.size_bytes),
    }

    async def _gen() -> Any:
        async for chunk in iter_download_file(
            manager.client, chat_id=cloud.chat_id, message_id=row.message_id
        ):
            yield chunk

    return StreamingResponse(
        _gen(),
        media_type=row.mime or "application/octet-stream",
        headers=headers,
    )


# ------------------------------------------------------------------ delete


@files_router.delete("/{file_id}", status_code=204, response_class=Response)
async def delete_file(
    file_id: int,
    user: CurrentUser,
    manager: UserbotManager = Depends(get_userbot_manager),
) -> Response:
    await _ensure_userbot_authorized(manager)
    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(File).where(
                    File.id == file_id, File.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "file_not_found"})
        if user.role != "admin" and row.owner_user_id != user.id:
            raise HTTPException(403, detail={"reason": "forbidden"})
        cloud = (
            await sess.execute(sa.select(Cloud).where(Cloud.id == row.cloud_id))
        ).scalar_one()
        size_to_release = row.size_bytes
        owner_user_id = row.owner_user_id

    try:
        await delete_file_message(
            manager.client, chat_id=cloud.chat_id, message_id=row.message_id
        )
    except Exception:
        logger.warning(
            "Telegram delete failed for file_id=%s; soft-deleting DB row anyway",
            file_id,
            exc_info=True,
        )

    cache = get_settings().data_dir / "tmp" / f"preview-{file_id}-800.jpg"
    with contextlib.suppress(FileNotFoundError):
        cache.unlink()

    async with sm() as sess:
        await sess.execute(
            sa.update(File)
            .where(File.id == file_id, File.deleted_at.is_(None))
            .values(deleted_at=sa.func.now())
        )
        await sess.commit()

    if owner_user_id is not None:
        await increment_used(owner_user_id, -size_to_release)
    return Response(status_code=204)


# ------------------------------------------------------------------ quota info


@files_router.get("/quota")
async def get_quota(user: CurrentUser) -> dict[str, Any]:
    used, quota = await get_used_and_quota(user.id)
    return {
        "used_bytes": used,
        "quota_bytes": quota,
        "free_bytes": max(0, quota - used),
    }


__all__ = ["clouds_files_router", "files_router"]
