"""Background Telegram backup for LCloud JSON DB operation logs."""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from telethon.tl.types import DocumentAttributeFilename

from lcloud.config import Settings, get_settings
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import JsonBackupSegment, JsonBackupState, JsonCollection, JsonOperation
from lcloud.userbot.client import UserbotManager, get_userbot_manager

logger = logging.getLogger(__name__)

BACKUP_FORMAT = "lcloud-json-db-segment-v1"
BACKUP_CAPTION_PREFIX = "LCDB1:"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class JsonDbBackupWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        manager: UserbotManager,
        interval_seconds: float,
        batch_limit: int,
    ) -> None:
        self.settings = settings
        self.manager = manager
        self.interval_seconds = interval_seconds
        self.batch_limit = batch_limit
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run(), name="lcloud-json-db-backup")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.backup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("json DB Telegram backup loop failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)

    async def backup_once(self) -> int:
        if not self.settings.lc_json_db_backup_enabled:
            return 0
        if not self.manager.is_started:
            return 0
        if not await self.manager.is_admin_authorized():
            return 0

        owner_ids = await _owners_with_pending_operations(self.batch_limit)
        uploaded = 0
        for owner_user_id in owner_ids:
            segment = await _build_segment_file(
                owner_user_id=owner_user_id,
                settings=self.settings,
                batch_limit=self.batch_limit,
            )
            if segment is None:
                continue
            path, meta = segment
            try:
                message_id = await _upload_segment_file(
                    manager=self.manager,
                    path=path,
                    meta=meta,
                )
                await _mark_segment_uploaded(meta=meta, message_id=message_id)
                uploaded += 1
            finally:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
        return uploaded


_worker: JsonDbBackupWorker | None = None


def start_json_db_backup_worker(settings: Settings | None = None) -> JsonDbBackupWorker | None:
    global _worker
    s = settings or get_settings()
    if not s.lc_json_db_backup_enabled:
        logger.info("json DB Telegram backup worker disabled")
        return None
    manager = get_userbot_manager()
    _worker = JsonDbBackupWorker(
        settings=s,
        manager=manager,
        interval_seconds=s.lc_json_db_backup_interval_seconds,
        batch_limit=s.lc_json_db_backup_batch_operations,
    )
    _worker.start()
    logger.info(
        "json DB Telegram backup worker started; interval=%ss batch=%s",
        s.lc_json_db_backup_interval_seconds,
        s.lc_json_db_backup_batch_operations,
    )
    return _worker


async def stop_json_db_backup_worker() -> None:
    global _worker
    worker = _worker
    _worker = None
    if worker is not None:
        await worker.stop()


async def run_json_db_backup_once(settings: Settings | None = None) -> int:
    s = settings or get_settings()
    manager = get_userbot_manager()
    worker = JsonDbBackupWorker(
        settings=s,
        manager=manager,
        interval_seconds=s.lc_json_db_backup_interval_seconds,
        batch_limit=s.lc_json_db_backup_batch_operations,
    )
    return await worker.backup_once()


async def get_json_db_backup_status(owner_user_id: int) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        latest_local = (
            await sess.execute(
                sa.select(sa.func.coalesce(sa.func.max(JsonOperation.id), 0))
                .select_from(JsonOperation)
                .join(JsonCollection, JsonCollection.id == JsonOperation.collection_id)
                .where(JsonCollection.owner_user_id == owner_user_id)
            )
        ).scalar_one()
        state = (
            await sess.execute(
                sa.select(JsonBackupState).where(
                    JsonBackupState.owner_user_id == owner_user_id
                )
            )
        ).scalar_one_or_none()
        last_segment = (
            await sess.execute(
                sa.select(JsonBackupSegment)
                .where(JsonBackupSegment.owner_user_id == owner_user_id)
                .order_by(JsonBackupSegment.last_operation_id.desc(), JsonBackupSegment.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    backed_up = state.last_operation_id if state is not None else 0
    return {
        "enabled": get_settings().lc_json_db_backup_enabled,
        "format": BACKUP_FORMAT,
        "target": "telegram_saved_messages",
        "last_local_operation_id": int(latest_local or 0),
        "last_backed_up_operation_id": int(backed_up),
        "lag_operations": max(0, int(latest_local or 0) - int(backed_up)),
        "last_backup_at": state.updated_at.isoformat() if state and state.updated_at else None,
        "last_segment": _serialize_segment(last_segment) if last_segment is not None else None,
    }


def _serialize_segment(row: JsonBackupSegment) -> dict[str, Any]:
    return {
        "id": row.id,
        "owner_user_id": row.owner_user_id,
        "first_operation_id": row.first_operation_id,
        "last_operation_id": row.last_operation_id,
        "operation_count": row.operation_count,
        "telegram_chat": row.telegram_chat,
        "telegram_message_id": row.telegram_message_id,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "status": row.status,
        "attempts": row.attempts,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None,
        "error": row.error,
    }


async def _owners_with_pending_operations(limit: int) -> list[int]:
    sm = get_sessionmaker()
    async with sm() as sess:
        rows = (
            await sess.execute(
                sa.select(JsonCollection.owner_user_id)
                .join(JsonOperation, JsonOperation.collection_id == JsonCollection.id)
                .outerjoin(
                    JsonBackupState,
                    JsonBackupState.owner_user_id == JsonCollection.owner_user_id,
                )
                .where(
                    JsonOperation.id
                    > sa.func.coalesce(JsonBackupState.last_operation_id, 0)
                )
                .group_by(JsonCollection.owner_user_id)
                .order_by(sa.func.min(JsonOperation.id).asc())
                .limit(limit)
            )
        ).all()
    return [int(row[0]) for row in rows]


async def _build_segment_file(
    *,
    owner_user_id: int,
    settings: Settings,
    batch_limit: int,
) -> tuple[Path, dict[str, Any]] | None:
    sm = get_sessionmaker()
    async with sm() as sess:
        state = (
            await sess.execute(
                sa.select(JsonBackupState).where(
                    JsonBackupState.owner_user_id == owner_user_id
                )
            )
        ).scalar_one_or_none()
        last_uploaded = state.last_operation_id if state is not None else 0
        rows = (
            await sess.execute(
                sa.select(JsonOperation, JsonCollection)
                .join(JsonCollection, JsonCollection.id == JsonOperation.collection_id)
                .where(
                    JsonCollection.owner_user_id == owner_user_id,
                    JsonOperation.id > last_uploaded,
                )
                .order_by(JsonOperation.id.asc())
                .limit(batch_limit)
            )
        ).all()

    if not rows:
        return None

    operations: list[dict[str, Any]] = []
    first_id = int(rows[0][0].id)
    last_id = int(rows[-1][0].id)
    for operation, collection in rows:
        operations.append(
            {
                "id": operation.id,
                "collection_id": operation.collection_id,
                "collection": {
                    "id": collection.id,
                    "owner_user_id": collection.owner_user_id,
                    "name": collection.name,
                    "read_rule": collection.read_rule,
                    "write_rule": collection.write_rule,
                    "write_validator_json": collection.write_validator_json,
                    "created_at": collection.created_at.isoformat() if collection.created_at else None,
                    "updated_at": collection.updated_at.isoformat() if collection.updated_at else None,
                },
                "doc_id": operation.doc_id,
                "owner_uid": operation.owner_uid,
                "op": operation.op,
                "payload": json.loads(operation.payload_json),
                "created_at": operation.created_at.isoformat() if operation.created_at else None,
            }
        )

    meta = {
        "format": BACKUP_FORMAT,
        "owner_user_id": owner_user_id,
        "first_operation_id": first_id,
        "last_operation_id": last_id,
        "operation_count": len(operations),
        "created_at_unix": int(time.time()),
    }
    payload = {"meta": meta, "operations": operations}
    raw = (_json_dumps(payload) + "\n").encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    sha = hashlib.sha256(compressed).hexdigest()
    meta["sha256"] = sha
    meta["size_bytes"] = len(compressed)
    filename = (
        f"lcloud-db-user-{owner_user_id}-ops-{first_id}-{last_id}-"
        f"{uuid.uuid4().hex[:8]}.json.gz"
    )
    path = settings.data_dir / "tmp" / filename
    path.write_bytes(compressed)
    return path, meta


async def _upload_segment_file(
    *,
    manager: UserbotManager,
    path: Path,
    meta: dict[str, Any],
) -> int:
    caption_meta = {
        "f": BACKUP_FORMAT,
        "u": meta["owner_user_id"],
        "a": meta["first_operation_id"],
        "b": meta["last_operation_id"],
        "n": meta["operation_count"],
        "h": meta["sha256"],
    }
    msg = await manager.client.send_file(
        "me",
        file=str(path),
        force_document=True,
        caption=f"{BACKUP_CAPTION_PREFIX}{_json_dumps(caption_meta)}",
        attributes=[DocumentAttributeFilename(file_name=path.name)],
    )
    return int(msg.id)


async def _mark_segment_uploaded(*, meta: dict[str, Any], message_id: int) -> None:
    sm = get_sessionmaker()
    async with sm() as sess:
        row = JsonBackupSegment(
            owner_user_id=int(meta["owner_user_id"]),
            first_operation_id=int(meta["first_operation_id"]),
            last_operation_id=int(meta["last_operation_id"]),
            operation_count=int(meta["operation_count"]),
            telegram_chat="me",
            telegram_message_id=message_id,
            sha256=str(meta["sha256"]),
            size_bytes=int(meta["size_bytes"]),
            status="uploaded",
            attempts=1,
            uploaded_at=sa.func.now(),
        )
        sess.add(row)
        state = (
            await sess.execute(
                sa.select(JsonBackupState).where(
                    JsonBackupState.owner_user_id == int(meta["owner_user_id"])
                )
            )
        ).scalar_one_or_none()
        if state is None:
            state = JsonBackupState(
                owner_user_id=int(meta["owner_user_id"]),
                last_operation_id=int(meta["last_operation_id"]),
            )
            sess.add(state)
        else:
            state.last_operation_id = max(
                state.last_operation_id,
                int(meta["last_operation_id"]),
            )
            state.updated_at = sa.func.now()
        await sess.commit()


__all__ = [
    "BACKUP_FORMAT",
    "JsonDbBackupWorker",
    "get_json_db_backup_status",
    "run_json_db_backup_once",
    "start_json_db_backup_worker",
    "stop_json_db_backup_worker",
]
