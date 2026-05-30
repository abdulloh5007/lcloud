"""Files router: upload / list / patch / download / thumb / delete cloud files."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

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
from fastapi import (
    Path as PathParam,
)
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from lcloud.auth.deps import require_admin
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

# Two routers: file actions on a cloud (under /clouds/{id}/files), and
# global file actions by id (under /files).
clouds_files_router = APIRouter(prefix="/clouds/{cloud_id}/files", tags=["files"])
files_router = APIRouter(prefix="/files", tags=["files"])


def _serialize_file(f: File) -> dict[str, Any]:
    return {
        "id": f.id,
        "cloud_id": f.cloud_id,
        "message_id": f.message_id,
        "name": f.original_name,
        "mime": f.mime,
        "size": f.size_bytes,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
        "deleted_at": f.deleted_at.isoformat() if f.deleted_at else None,
    }


async def _get_cloud_or_404(cloud_id: int, owner_id: int) -> Cloud:
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(Cloud).where(
                Cloud.id == cloud_id, Cloud.owner_id == owner_id
            )
        )
        cloud = result.scalar_one_or_none()
    if cloud is None:
        raise HTTPException(404, detail={"reason": "cloud_not_found"})
    return cloud


async def _ensure_userbot_authorized(manager: UserbotManager) -> None:
    if not manager.is_started:
        raise HTTPException(503, detail={"reason": "userbot_not_started"})
    if not await manager.is_admin_authorized():
        raise HTTPException(409, detail={"reason": "userbot_not_authorized"})


# ------------------------------------------------------------------ list


@clouds_files_router.get("")
async def list_files(
    cloud_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    owner_id: int = Depends(require_admin),
) -> dict[str, Any]:
    """Paginated listing. Returns ``{items, total, limit, offset}`` so the
    UI can render an "infinite scroll" / lazy-loaded grid without first
    fetching the entire cloud."""
    cloud = await _get_cloud_or_404(cloud_id, owner_id)
    sm = get_sessionmaker()
    async with sm() as sess:
        total = (
            await sess.execute(
                sa.select(sa.func.count())
                .select_from(File)
                .where(File.cloud_id == cloud.id, File.deleted_at.is_(None))
            )
        ).scalar_one()
        rows = (
            await sess.execute(
                sa.select(File)
                .where(File.cloud_id == cloud.id, File.deleted_at.is_(None))
                .order_by(File.uploaded_at.desc(), File.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    return {
        "items": [_serialize_file(f) for f in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


# ------------------------------------------------------------------ upload


async def _stream_to_temp_async(
    upload: UploadFile, tmp_dir: Path, max_size: int
) -> tuple[Path, int, bytes]:
    """Stream `upload` to a temp file under `tmp_dir`; return path, size, sha256.

    Aborts with 413 if size exceeds `max_size`. Caller owns the resulting
    path and is responsible for unlinking it.
    """
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


@clouds_files_router.post("", status_code=201)
async def upload_file(
    cloud_id: int,
    file: UploadFile = FileParam(...),
    owner_id: int = Depends(require_admin),
    manager: UserbotManager = Depends(get_userbot_manager),
) -> dict[str, Any]:
    """Multipart upload to the given cloud chat. Body limit: LC_MAX_FILE_BYTES."""
    await _ensure_userbot_authorized(manager)
    cloud = await _get_cloud_or_404(cloud_id, owner_id)
    settings = get_settings()
    sk, _ = ensure_admin_keypair(settings)
    pool = get_worker_pool()

    tmp_path, size, sha = await _stream_to_temp_async(
        file, settings.data_dir / "tmp", settings.lc_max_file_bytes
    )

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
    except Exception:
        logger.exception("upload to Telegram failed")
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise HTTPException(
            502, detail={"reason": "telegram_upload_failed"}
        ) from None
    else:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()

    sm = get_sessionmaker()
    async with sm() as sess:
        row = File(
            cloud_id=cloud.id,
            message_id=result.message_id,
            owner_id=owner_id,
            original_name=file.filename or f"file-{uuid.uuid4().hex}",
            mime=file.content_type or "application/octet-stream",
            size_bytes=size,
            sha256=sha,
            signature=result.signature,
        )
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)
    return _serialize_file(row)


# ------------------------------------------------------------------ download


@files_router.get("/{file_id}/download")
async def download_file(
    file_id: int = PathParam(..., ge=1),
    owner_id: int = Depends(require_admin),
    manager: UserbotManager = Depends(get_userbot_manager),
) -> StreamingResponse:
    await _ensure_userbot_authorized(manager)
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(File).where(
                File.id == file_id,
                File.owner_id == owner_id,
                File.deleted_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "file_not_found"})
        cloud_result = await sess.execute(
            sa.select(Cloud).where(Cloud.id == row.cloud_id)
        )
        cloud = cloud_result.scalar_one_or_none()
        if cloud is None:
            raise HTTPException(500, detail={"reason": "cloud_row_missing"})
        chat_id = cloud.chat_id
        message_id = row.message_id
        original_name = row.original_name
        mime = row.mime
        size_bytes = row.size_bytes

    async def _stream() -> AsyncIterator[bytes]:
        async for chunk in iter_download_file(
            manager.client, chat_id=chat_id, message_id=message_id
        ):
            yield chunk

    safe_name = quote(original_name, safe="")
    return StreamingResponse(
        _stream(),
        media_type=mime,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{original_name}"; '
                f"filename*=UTF-8''{safe_name}"
            ),
            "Content-Length": str(size_bytes),
        },
    )


# ------------------------------------------------------------------ rename


class FilePatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=512)


@files_router.patch("/{file_id}")
async def rename_file(
    file_id: int,
    body: FilePatchIn,
    owner_id: int = Depends(require_admin),
) -> dict[str, Any]:
    """Rename `original_name` of a file in our DB. The Telegram message
    itself is unchanged — the LC1 caption (sha + sig + ts) stays canonical
    metadata; the human-readable name is purely a DB field."""
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(File).where(
                File.id == file_id,
                File.owner_id == owner_id,
                File.deleted_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "file_not_found"})
        # Strip control chars / leading-trailing whitespace for safety
        new_name = body.name.strip().replace("\r", "").replace("\n", " ")
        if not new_name:
            raise HTTPException(422, detail={"reason": "empty_name"})
        row.original_name = new_name
        await sess.commit()
        await sess.refresh(row)
    return _serialize_file(row)


# ------------------------------------------------------------------ thumb


@files_router.get("/{file_id}/thumb")
async def file_thumb(
    file_id: int,
    size: Literal["low", "med", "high"] = Query("low"),
    owner_id: int = Depends(require_admin),
    manager: UserbotManager = Depends(get_userbot_manager),
) -> Response:
    """Serve a downscaled preview.

    - `low`  — Telegram-generated document thumbnail (~160 px JPEG, very small)
    - `med`  — Pillow-scaled to fit 800x800 (cached on disk; ~50-200 KB)
    - `high` — 302 to /download (original file)

    Non-image documents (and any failure to produce a preview) fall back to
    a 302 redirect to `/download`.
    """
    if size == "high":
        return RedirectResponse(f"/files/{file_id}/download", status_code=302)

    await _ensure_userbot_authorized(manager)
    settings = get_settings()
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(File).where(
                File.id == file_id,
                File.owner_id == owner_id,
                File.deleted_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "file_not_found"})
        cloud_result = await sess.execute(
            sa.select(Cloud).where(Cloud.id == row.cloud_id)
        )
        cloud = cloud_result.scalar_one_or_none()
        if cloud is None:
            raise HTTPException(500, detail={"reason": "cloud_row_missing"})
        chat_id = cloud.chat_id
        message_id = row.message_id
        mime = row.mime

    # Only image/* documents have meaningful previews. For everything else,
    # short-circuit to /download.
    if not mime.startswith("image/"):
        return RedirectResponse(f"/files/{file_id}/download", status_code=302)

    # ---- size=med: server-side Pillow resize, cached on disk ----------------
    if size == "med":
        cache_dir = settings.data_dir / "tmp"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"preview-{file_id}-800.jpg"
        if cache_path.exists():
            return FileResponse(
                cache_path,
                media_type="image/jpeg",
                headers={"Cache-Control": "private, max-age=3600"},
            )
        try:
            entity = await manager.client.get_entity(chat_id)
            msg = await manager.client.get_messages(entity, ids=message_id)
            if msg is None:
                return RedirectResponse(
                    f"/files/{file_id}/download", status_code=302
                )
            data = await manager.client.download_media(msg, file=bytes)
            if not isinstance(data, bytes | bytearray):
                return RedirectResponse(
                    f"/files/{file_id}/download", status_code=302
                )
            from io import BytesIO

            from PIL import Image, ImageOps

            img: Image.Image = Image.open(BytesIO(bytes(data)))
            img = ImageOps.exif_transpose(img) or img
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail((800, 800), Image.Resampling.LANCZOS)
            img.save(cache_path, "JPEG", quality=82, optimize=True)
        except Exception:
            logger.exception("med-thumb generation failed for file %s", file_id)
            return RedirectResponse(
                f"/files/{file_id}/download", status_code=302
            )
        return FileResponse(
            cache_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    # ---- size=low: Telegram-side document thumb (one-shot, very small) ------
    try:
        entity = await manager.client.get_entity(chat_id)
        msg = await manager.client.get_messages(entity, ids=message_id)
    except Exception:
        logger.exception("thumb: get_messages failed for file %s", file_id)
        return RedirectResponse(f"/files/{file_id}/download", status_code=302)
    if msg is None:
        return RedirectResponse(f"/files/{file_id}/download", status_code=302)

    doc = getattr(msg, "document", None)
    thumbs = (getattr(doc, "thumbs", None) or []) if doc is not None else []
    if not thumbs:
        return RedirectResponse(f"/files/{file_id}/download", status_code=302)

    try:
        data = await manager.client.download_media(msg, thumb=0, file=bytes)
    except Exception:
        logger.exception("thumb: download_media failed for file %s", file_id)
        return RedirectResponse(f"/files/{file_id}/download", status_code=302)
    if not isinstance(data, bytes | bytearray):
        return RedirectResponse(f"/files/{file_id}/download", status_code=302)

    return Response(
        content=bytes(data),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


# ------------------------------------------------------------------ delete


@files_router.delete("/{file_id}", status_code=204, response_class=Response)
async def delete_file(
    file_id: int,
    owner_id: int = Depends(require_admin),
    manager: UserbotManager = Depends(get_userbot_manager),
) -> Response:
    await _ensure_userbot_authorized(manager)
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(File).where(
                File.id == file_id,
                File.owner_id == owner_id,
                File.deleted_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "file_not_found"})
        cloud_result = await sess.execute(
            sa.select(Cloud).where(Cloud.id == row.cloud_id)
        )
        cloud = cloud_result.scalar_one_or_none()
        if cloud is None:
            raise HTTPException(500, detail={"reason": "cloud_row_missing"})

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

    # Best-effort: drop any cached med-thumb for this file
    cache = get_settings().data_dir / "tmp" / f"preview-{file_id}-800.jpg"
    with contextlib.suppress(FileNotFoundError):
        cache.unlink()

    async with sm() as sess:
        result = await sess.execute(sa.select(File).where(File.id == file_id))
        row = result.scalar_one_or_none()
        if row is not None and row.deleted_at is None:
            row.deleted_at = sa.func.now()
            await sess.commit()
    return Response(status_code=204)
