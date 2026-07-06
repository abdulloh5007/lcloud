"""Publishable storage keys for browser/serverless media uploads."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi import File as FileParam
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from lcloud.api.compression import compress_image_in_place, is_compressible_mime
from lcloud.api.compression_video import compress_video_in_place, is_compressible_video_mime
from lcloud.api.json_databases import DatabaseKeyQuery, resolve_database
from lcloud.api.v2_files import _ensure_userbot_authorized, _serialize, _stream_to_temp
from lcloud.auth.storage_quota import assert_can_store, increment_used
from lcloud.auth.v2_deps import CurrentUser
from lcloud.cache import (
    PUBLIC_KEY_TTL,
    cache,
    invalidate_files_in_cloud,
    invalidate_json_storage_keys,
    invalidate_user_quota,
    k_json_storage_key,
)
from lcloud.config import get_settings
from lcloud.crypto.keys import ensure_admin_keypair
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import Cloud, File, JsonDatabase, StoragePublicKey, User
from lcloud.metrics import uploaded_bytes_counter, uploads_counter
from lcloud.userbot.client import UserbotManager, get_userbot_manager
from lcloud.userbot.files import delete_file_message, iter_download_file, upload_file_to_cloud
from lcloud.utils.rate_limit import RateLimiter
from lcloud.workers import get_worker_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/storage/public-keys", tags=["storage_public_keys"])
public_router = APIRouter(prefix="/api/v1/public/storage/key/{storage_key}", tags=["storage_public"])

STORAGE_PUBLIC_KEY_PREFIX = "lstore_"
STORAGE_PUBLIC_KEY_PREFIX_LEN = len(STORAGE_PUBLIC_KEY_PREFIX) + 8
STORAGE_PUBLIC_KEY_ENTROPY_LEN = 32
STORAGE_PUBLIC_KEY_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
MAX_STORAGE_PUBLIC_KEYS_PER_USER = 25
PUBLIC_STORAGE_READ_RATE_LIMIT = 120
PUBLIC_STORAGE_WRITE_RATE_LIMIT = 20
PUBLIC_STORAGE_RATE_WINDOW_SECONDS = 60

_public_storage_read_rate = RateLimiter(
    capacity=PUBLIC_STORAGE_READ_RATE_LIMIT,
    refill_seconds=PUBLIC_STORAGE_RATE_WINDOW_SECONDS,
)
_public_storage_write_rate = RateLimiter(
    capacity=PUBLIC_STORAGE_WRITE_RATE_LIMIT,
    refill_seconds=PUBLIC_STORAGE_RATE_WINDOW_SECONDS,
)


class StoragePublicKeyIn(BaseModel):
    cloud_id: int | None = Field(default=None, ge=1)
    database_id: int | None = Field(default=None, ge=1)
    database_key: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^lcdb_[a-z2-9]{24}$"
    )
    label: str = Field(default="", max_length=64)
    allow_upload: bool = True
    allow_list: bool = True
    allow_download: bool = True
    allow_delete: bool = False
    max_file_bytes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_permissions(self) -> StoragePublicKeyIn:
        if not any([self.allow_upload, self.allow_list, self.allow_download, self.allow_delete]):
            raise ValueError("at_least_one_permission_required")
        if self.cloud_id is None and self.database_id is None and self.database_key is None:
            raise ValueError("cloud_id_or_database_id_or_database_key_required")
        return self


def _now() -> datetime:
    return datetime.now(UTC)


def _new_storage_public_key() -> str:
    body = "".join(
        secrets.choice(STORAGE_PUBLIC_KEY_ALPHABET)
        for _ in range(STORAGE_PUBLIC_KEY_ENTROPY_LEN)
    )
    return f"{STORAGE_PUBLIC_KEY_PREFIX}{body}"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_public_storage_rate(request: Request, *, action: str) -> None:
    limiter = _public_storage_read_rate if action == "read" else _public_storage_write_rate
    limit = PUBLIC_STORAGE_READ_RATE_LIMIT if action == "read" else PUBLIC_STORAGE_WRITE_RATE_LIMIT
    if not limiter.try_acquire(f"storage:{action}:{_client_ip(request)}"):
        raise HTTPException(
            429,
            detail={
                "reason": "rate_limited",
                "scope": f"public_storage_{action}",
                "limit": limit,
                "window_seconds": PUBLIC_STORAGE_RATE_WINDOW_SECONDS,
            },
        )


def _serialize_key(row: StoragePublicKey) -> dict[str, Any]:
    return {
        "id": row.id,
        "database_id": row.database_id,
        "cloud_id": row.cloud_id,
        "key": row.key,
        "prefix": row.prefix,
        "label": row.label,
        "allow_upload": row.allow_upload,
        "allow_list": row.allow_list,
        "allow_download": row.allow_download,
        "allow_delete": row.allow_delete,
        "max_file_bytes": row.max_file_bytes,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


async def _get_cloud_for_owner(cloud_id: int, user: User) -> Cloud:
    sm = get_sessionmaker()
    async with sm() as sess:
        cloud = (
            await sess.execute(sa.select(Cloud).where(Cloud.id == cloud_id))
        ).scalar_one_or_none()
    if cloud is None:
        raise HTTPException(404, detail={"reason": "cloud_not_found"})
    if user.role != "admin" and cloud.owner_user_id != user.id:
        raise HTTPException(404, detail={"reason": "cloud_not_found"})
    return cloud


async def _load_public_storage_context(storage_key: str) -> tuple[StoragePublicKey, Cloud, User]:
    cached = await cache.get(k_json_storage_key(storage_key))
    if isinstance(cached, dict):
        key = SimpleNamespace(**cached["key"])
        cloud = SimpleNamespace(**cached["cloud"])
        user = SimpleNamespace(**cached["user"])
        if getattr(user, "suspended_at", None) is not None:
            raise HTTPException(403, detail={"reason": "suspended"})
        return key, cloud, user  # type: ignore[return-value]

    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(StoragePublicKey).where(
                    StoragePublicKey.key == storage_key,
                    StoragePublicKey.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "storage_key_not_found"})
        cloud = (
            await sess.execute(sa.select(Cloud).where(Cloud.id == row.cloud_id))
        ).scalar_one_or_none()
        user = (
            await sess.execute(sa.select(User).where(User.id == row.owner_user_id))
        ).scalar_one_or_none()
    if cloud is None or user is None:
        raise HTTPException(404, detail={"reason": "storage_key_not_found"})
    if user.suspended_at is not None:
        raise HTTPException(403, detail={"reason": "suspended"})
    if cloud.owner_user_id != row.owner_user_id:
        raise HTTPException(403, detail={"reason": "storage_key_cloud_mismatch"})
    if row.database_id is not None:
        sm = get_sessionmaker()
        async with sm() as sess:
            database = (
                await sess.execute(
                    sa.select(JsonDatabase).where(JsonDatabase.id == row.database_id)
                )
            ).scalar_one_or_none()
        if database is None or database.cloud_id != cloud.id:
            raise HTTPException(403, detail={"reason": "storage_key_database_mismatch"})
    await cache.set(
        k_json_storage_key(storage_key),
        {
            "key": {
                "id": row.id,
                "database_id": row.database_id,
                "owner_user_id": row.owner_user_id,
                "cloud_id": row.cloud_id,
                "allow_upload": row.allow_upload,
                "allow_list": row.allow_list,
                "allow_download": row.allow_download,
                "allow_delete": row.allow_delete,
                "max_file_bytes": row.max_file_bytes,
            },
            "cloud": {
                "id": cloud.id,
                "chat_id": cloud.chat_id,
                "owner_user_id": cloud.owner_user_id,
            },
            "user": {
                "id": user.id,
                "suspended_at": (
                    user.suspended_at.isoformat() if user.suspended_at else None
                ),
            },
        },
        ttl=PUBLIC_KEY_TTL,
        namespace="json_storage_key",
    )
    return row, cloud, user


@router.get("")
async def list_storage_public_keys(
    user: CurrentUser,
    database_id: int | None = Query(default=None, ge=1),
    database_key: DatabaseKeyQuery = None,
) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        query = sa.select(StoragePublicKey).where(StoragePublicKey.owner_user_id == user.id)
        if database_key is not None:
            database = await resolve_database(
                sess, user=user, database_id=database_id, database_key=database_key
            )
            database_id = database.id
        if database_id is not None:
            query = query.where(StoragePublicKey.database_id == database_id)
        rows = (
            await sess.execute(
                query.order_by(
                    StoragePublicKey.created_at.desc(), StoragePublicKey.id.desc()
                )
            )
        ).scalars().all()
    return [_serialize_key(row) for row in rows]


@router.post("", status_code=201)
async def create_storage_public_key(body: StoragePublicKeyIn, user: CurrentUser) -> dict[str, Any]:
    settings = get_settings()
    max_file_bytes = body.max_file_bytes or settings.lc_max_file_bytes
    if max_file_bytes > settings.lc_max_file_bytes:
        raise HTTPException(
            422,
            detail={
                "reason": "max_file_bytes_too_large",
                "limit": settings.lc_max_file_bytes,
            },
        )
    cloud_id = body.cloud_id
    resolved_database_id = body.database_id
    if body.database_id is not None or body.database_key is not None:
        sm = get_sessionmaker()
        async with sm() as sess:
            database = await resolve_database(
                sess,
                user=user,
                database_id=body.database_id,
                database_key=body.database_key,
            )
        if database.cloud_id is None:
            raise HTTPException(409, detail={"reason": "database_not_telegram_backed"})
        if cloud_id is not None and cloud_id != database.cloud_id:
            raise HTTPException(422, detail={"reason": "database_cloud_mismatch"})
        cloud_id = database.cloud_id
        resolved_database_id = database.id
    assert cloud_id is not None
    await _get_cloud_for_owner(cloud_id, user)

    sm = get_sessionmaker()
    async with sm() as sess:
        active = (
            await sess.execute(
                sa.select(sa.func.count())
                .select_from(StoragePublicKey)
                .where(
                    StoragePublicKey.owner_user_id == user.id,
                    StoragePublicKey.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        if active >= MAX_STORAGE_PUBLIC_KEYS_PER_USER:
            raise HTTPException(
                400,
                detail={
                    "reason": "storage_key_limit_reached",
                    "max": MAX_STORAGE_PUBLIC_KEYS_PER_USER,
                },
            )

        key = _new_storage_public_key()
        while (
            await sess.execute(sa.select(StoragePublicKey.id).where(StoragePublicKey.key == key))
        ).scalar_one_or_none():
            key = _new_storage_public_key()

        row = StoragePublicKey(
            database_id=resolved_database_id,
            owner_user_id=user.id,
            cloud_id=cloud_id,
            key=key,
            prefix=key[:STORAGE_PUBLIC_KEY_PREFIX_LEN],
            label=body.label.strip(),
            allow_upload=body.allow_upload,
            allow_list=body.allow_list,
            allow_download=body.allow_download,
            allow_delete=body.allow_delete,
            max_file_bytes=max_file_bytes,
        )
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)
    await invalidate_json_storage_keys()
    return _serialize_key(row)


@router.delete("/{key_id}")
async def revoke_storage_public_key(key_id: int, user: CurrentUser) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(StoragePublicKey).where(
                    StoragePublicKey.id == key_id,
                    StoragePublicKey.owner_user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "storage_key_not_found"})
        if row.revoked_at is None:
            row.revoked_at = _now()
            await sess.commit()
    await invalidate_json_storage_keys()
    return {"ok": True}


@public_router.get("/files")
async def public_list_files(
    storage_key: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _enforce_public_storage_rate(request, action="read")
    key, cloud, _user = await _load_public_storage_context(storage_key)
    if not key.allow_list:
        raise HTTPException(403, detail={"reason": "storage_key_list_disabled"})

    sm = get_sessionmaker()
    async with sm() as sess:
        cond = sa.and_(
            File.cloud_id == cloud.id,
            File.owner_user_id == key.owner_user_id,
            File.deleted_at.is_(None),
        )
        total = (
            await sess.execute(sa.select(sa.func.count()).select_from(File).where(cond))
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
        "items": [_serialize(row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@public_router.post("/files", status_code=201)
async def public_upload_file(
    storage_key: str,
    request: Request,
    file: UploadFile = FileParam(...),
    manager: UserbotManager = Depends(get_userbot_manager),
    compress: Annotated[bool, Form()] = True,
) -> dict[str, Any]:
    _enforce_public_storage_rate(request, action="write")
    key, cloud, user = await _load_public_storage_context(storage_key)
    if not key.allow_upload:
        raise HTTPException(403, detail={"reason": "storage_key_upload_disabled"})
    await _ensure_userbot_authorized(manager)

    settings = get_settings()
    max_file_bytes = min(key.max_file_bytes or settings.lc_max_file_bytes, settings.lc_max_file_bytes)
    pool = get_worker_pool()
    tmp_path, original_size, original_sha = await _stream_to_temp(
        file, settings.data_dir / "tmp", max_file_bytes
    )
    final_path = tmp_path
    final_size = original_size
    final_mime = file.content_type or "application/octet-stream"
    final_sha = original_sha
    was_compressed = False

    if compress and is_compressible_mime(final_mime):
        try:
            final_path, final_size, final_mime, was_compressed = compress_image_in_place(
                tmp_path, mime=final_mime
            )
            if was_compressed:
                final_sha = hashlib.sha256(final_path.read_bytes()).digest()
        except Exception as exc:
            logger.warning("public storage image compression failed: %s", exc)
            final_path = tmp_path
            final_size = original_size
            final_mime = file.content_type or "application/octet-stream"
            final_sha = original_sha
            was_compressed = False
    elif compress and is_compressible_video_mime(final_mime):
        try:
            final_path, final_size, final_mime, was_compressed = compress_video_in_place(
                tmp_path, mime=final_mime
            )
            if was_compressed:
                final_sha = hashlib.sha256(final_path.read_bytes()).digest()
        except Exception as exc:
            logger.warning("public storage video compression failed: %s", exc)
            final_path = tmp_path
            final_size = original_size
            final_mime = file.content_type or "application/octet-stream"
            final_sha = original_sha
            was_compressed = False

    if final_size > max_file_bytes:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        if final_path != tmp_path:
            with contextlib.suppress(FileNotFoundError):
                final_path.unlink()
        raise HTTPException(
            413,
            detail={"reason": "file_too_large", "size": final_size, "limit": max_file_bytes},
        )

    try:
        await assert_can_store(user.id, final_size)
    except HTTPException:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        if final_path != tmp_path:
            with contextlib.suppress(FileNotFoundError):
                final_path.unlink()
        raise

    sk, _ = ensure_admin_keypair(settings)
    try:
        result = await pool.submit(
            upload_file_to_cloud(
                manager.client,
                chat_id=cloud.chat_id,
                file_path=final_path,
                original_name=file.filename or f"file-{uuid.uuid4().hex}",
                sha256_digest=final_sha,
                signing_key=sk,
            )
        )
    except Exception as exc:
        logger.exception("public storage upload to Telegram failed")
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        if final_path != tmp_path:
            with contextlib.suppress(FileNotFoundError):
                final_path.unlink()
        raise HTTPException(
            502,
            detail={"reason": "telegram_upload_failed", "error": str(exc)},
        ) from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        if final_path != tmp_path:
            with contextlib.suppress(FileNotFoundError):
                final_path.unlink()

    name = file.filename or f"file-{uuid.uuid4().hex}"
    sm = get_sessionmaker()
    async with sm() as sess:
        prev = (
            await sess.execute(
                sa.select(File).where(
                    File.cloud_id == cloud.id,
                    File.owner_user_id == user.id,
                    File.original_name == name,
                    File.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        replaces_id: int | None = None
        if prev is not None:
            prev.deleted_at = sa.func.now()
            replaces_id = prev.id
        row = File(
            cloud_id=cloud.id,
            message_id=result.message_id,
            owner_id=cloud.owner_id,
            owner_user_id=user.id,
            original_name=name,
            mime=final_mime,
            size_bytes=final_size,
            sha256=final_sha,
            signature=result.signature,
            compressed=was_compressed,
            original_size_bytes=original_size if was_compressed else None,
            replaces_file_id=replaces_id,
        )
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)

    await increment_used(user.id, final_size)
    await invalidate_user_quota(user.id)
    await invalidate_files_in_cloud(cloud.id)
    mime_class = final_mime.split("/")[0] if "/" in final_mime else "other"
    uploads_counter.labels(
        mime_class=mime_class,
        compressed=str(was_compressed).lower(),
        caption_kind="LC1",
    ).inc()
    uploaded_bytes_counter.inc(final_size)

    out = _serialize(row)
    out["caption_kind"] = "LC1"
    out["uploaded_at_unix"] = result.uploaded_at_unix
    if was_compressed:
        out["compression_ratio"] = round(final_size / original_size, 3)
    return out


@public_router.get("/files/{file_id}/download")
async def public_download_file(
    storage_key: str,
    file_id: int,
    request: Request,
    manager: UserbotManager = Depends(get_userbot_manager),
) -> StreamingResponse:
    _enforce_public_storage_rate(request, action="read")
    key, cloud, _user = await _load_public_storage_context(storage_key)
    if not key.allow_download:
        raise HTTPException(403, detail={"reason": "storage_key_download_disabled"})
    await _ensure_userbot_authorized(manager)

    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(File).where(
                    File.id == file_id,
                    File.cloud_id == cloud.id,
                    File.owner_user_id == key.owner_user_id,
                    File.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "not_found"})

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


@public_router.delete("/files/{file_id}", status_code=204, response_class=Response)
async def public_delete_file(
    storage_key: str,
    file_id: int,
    request: Request,
    manager: UserbotManager = Depends(get_userbot_manager),
) -> Response:
    _enforce_public_storage_rate(request, action="write")
    key, cloud, _user = await _load_public_storage_context(storage_key)
    if not key.allow_delete:
        raise HTTPException(403, detail={"reason": "storage_key_delete_disabled"})
    await _ensure_userbot_authorized(manager)

    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(File).where(
                    File.id == file_id,
                    File.cloud_id == cloud.id,
                    File.owner_user_id == key.owner_user_id,
                    File.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "not_found"})
        size_to_release = row.size_bytes

    try:
        await delete_file_message(
            manager.client, chat_id=cloud.chat_id, message_id=row.message_id
        )
    except Exception:
        logger.warning("public storage Telegram delete failed", exc_info=True)

    async with sm() as sess:
        await sess.execute(
            sa.update(File)
            .where(File.id == file_id, File.deleted_at.is_(None))
            .values(deleted_at=sa.func.now())
        )
        await sess.commit()

    await increment_used(key.owner_user_id, -size_to_release)
    await invalidate_user_quota(key.owner_user_id)
    await invalidate_files_in_cloud(cloud.id)
    return Response(status_code=204)


def reset_storage_public_rate_limits() -> None:
    _public_storage_read_rate.reset()
    _public_storage_write_rate.reset()


__all__ = [
    "MAX_STORAGE_PUBLIC_KEYS_PER_USER",
    "PUBLIC_STORAGE_RATE_WINDOW_SECONDS",
    "PUBLIC_STORAGE_READ_RATE_LIMIT",
    "PUBLIC_STORAGE_WRITE_RATE_LIMIT",
    "STORAGE_PUBLIC_KEY_PREFIX",
    "public_router",
    "reset_storage_public_rate_limits",
    "router",
]
