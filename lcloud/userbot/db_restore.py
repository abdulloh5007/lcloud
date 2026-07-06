"""Restore LCloud JSON DB operations from Telegram LCDB1 backup segments."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa

from lcloud.config import get_settings
from lcloud.db.base import dispose_engine, get_sessionmaker, init_engine
from lcloud.db.bootstrap import run_migrations
from lcloud.db.models import (
    JsonBackupSegment,
    JsonBackupState,
    JsonCollection,
    JsonDatabase,
    JsonDocument,
    JsonOperation,
    User,
)
from lcloud.userbot.client import UserbotManager
from lcloud.userbot.db_backup import BACKUP_CAPTION_PREFIX, BACKUP_FORMAT, _json_dumps

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramBackupSegment:
    source_owner_user_id: int
    first_operation_id: int
    last_operation_id: int
    operation_count: int
    sha256: str
    message_id: int
    compressed: bytes
    payload: dict[str, Any]


@dataclass(frozen=True)
class RestoreResult:
    segments: int
    operations: int
    first_operation_id: int | None
    last_operation_id: int | None
    dry_run: bool


class RestoreError(RuntimeError):
    pass


def _message_text(message: Any) -> str:
    value = getattr(message, "message", None) or getattr(message, "text", None) or ""
    return str(value)


def _message_id(message: Any) -> int:
    return int(getattr(message, "id", 0) or 0)


def parse_lcdb1_caption(text: str) -> dict[str, Any] | None:
    if not text.startswith(BACKUP_CAPTION_PREFIX):
        return None
    try:
        parsed = json.loads(text[len(BACKUP_CAPTION_PREFIX) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or parsed.get("f") != BACKUP_FORMAT:
        return None
    return parsed


async def discover_lcdb1_segments(
    client: Any,
    *,
    entity: Any = "me",
    source_owner_user_id: int | None = None,
    source_database_id: int | None = None,
    limit: int | None = None,
) -> list[TelegramBackupSegment]:
    segments: list[TelegramBackupSegment] = []
    kwargs: dict[str, Any] = {}
    if limit is not None:
        kwargs["limit"] = limit
    async for message in client.iter_messages(entity, search=BACKUP_CAPTION_PREFIX, **kwargs):
        meta = parse_lcdb1_caption(_message_text(message))
        if meta is None:
            continue
        owner = int(meta["u"])
        if source_owner_user_id is not None and owner != source_owner_user_id:
            continue
        if source_database_id is not None and int(meta.get("d", 0)) != source_database_id:
            continue
        compressed = await client.download_media(message, file=bytes)
        if not isinstance(compressed, bytes):
            raise RestoreError(f"message {_message_id(message)} did not download bytes")
        sha = hashlib.sha256(compressed).hexdigest()
        expected_sha = str(meta["h"])
        if sha != expected_sha:
            raise RestoreError(
                f"message {_message_id(message)} checksum mismatch: {sha} != {expected_sha}"
            )
        try:
            payload = json.loads(gzip.decompress(compressed).decode("utf-8"))
        except Exception as exc:
            raise RestoreError(f"message {_message_id(message)} invalid gzip/json") from exc
        payload_meta = payload.get("meta") if isinstance(payload, dict) else None
        operations = payload.get("operations") if isinstance(payload, dict) else None
        if not isinstance(payload_meta, dict) or not isinstance(operations, list):
            raise RestoreError(f"message {_message_id(message)} invalid segment shape")
        if payload_meta.get("format") != BACKUP_FORMAT:
            raise RestoreError(f"message {_message_id(message)} invalid format")
        first_id = int(meta["a"])
        last_id = int(meta["b"])
        count = int(meta["n"])
        if int(payload_meta.get("first_operation_id", -1)) != first_id:
            raise RestoreError(f"message {_message_id(message)} first operation mismatch")
        if int(payload_meta.get("last_operation_id", -1)) != last_id:
            raise RestoreError(f"message {_message_id(message)} last operation mismatch")
        if len(operations) != count:
            raise RestoreError(f"message {_message_id(message)} operation count mismatch")
        segments.append(
            TelegramBackupSegment(
                source_owner_user_id=owner,
                first_operation_id=first_id,
                last_operation_id=last_id,
                operation_count=count,
                sha256=sha,
                message_id=_message_id(message),
                compressed=compressed,
                payload=payload,
            )
        )
    return _dedupe_segments(segments)


def _dedupe_segments(segments: list[TelegramBackupSegment]) -> list[TelegramBackupSegment]:
    by_range: dict[tuple[int, int, int], TelegramBackupSegment] = {}
    for segment in segments:
        key = (
            segment.source_owner_user_id,
            segment.first_operation_id,
            segment.last_operation_id,
        )
        current = by_range.get(key)
        if current is None or segment.message_id > current.message_id:
            by_range[key] = segment
    return sorted(
        by_range.values(),
        key=lambda item: (item.source_owner_user_id, item.first_operation_id, item.last_operation_id),
    )


async def restore_json_db_from_telegram(
    client: Any,
    *,
    target_owner_user_id: int,
    entity: Any = "me",
    source_owner_user_id: int | None = None,
    source_database_id: int | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> RestoreResult:
    segments = await discover_lcdb1_segments(
        client,
        entity=entity,
        source_owner_user_id=source_owner_user_id,
        source_database_id=source_database_id,
        limit=limit,
    )
    return await restore_json_db_segments(
        segments,
        target_owner_user_id=target_owner_user_id,
        dry_run=dry_run,
    )


async def restore_json_db_segments(
    segments: list[TelegramBackupSegment],
    *,
    target_owner_user_id: int,
    dry_run: bool = False,
) -> RestoreResult:
    if not segments:
        return RestoreResult(segments=0, operations=0, first_operation_id=None, last_operation_id=None, dry_run=dry_run)

    operations: list[tuple[TelegramBackupSegment, dict[str, Any]]] = []
    for segment in segments:
        raw_ops = segment.payload.get("operations")
        if not isinstance(raw_ops, list):
            raise RestoreError("segment operations must be a list")
        for raw in raw_ops:
            if not isinstance(raw, dict):
                raise RestoreError("operation must be an object")
            operations.append((segment, raw))

    operations.sort(key=lambda item: int(item[1]["id"]))
    first_id = int(operations[0][1]["id"])
    last_id = int(operations[-1][1]["id"])

    sm = get_sessionmaker()
    async with sm() as sess:
        exists = (
            await sess.execute(sa.select(User.id).where(User.id == target_owner_user_id))
        ).scalar_one_or_none()
        if exists is None:
            raise RestoreError(f"target user id {target_owner_user_id} does not exist")
        database = (
            await sess.execute(
                sa.select(JsonDatabase)
                .where(JsonDatabase.owner_user_id == target_owner_user_id)
                .order_by(JsonDatabase.is_default.desc(), JsonDatabase.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if database is None:
            database = JsonDatabase(
                owner_user_id=target_owner_user_id,
                name="Restored database",
                is_default=True,
            )
            sess.add(database)
            await sess.flush()

        collection_map: dict[int, JsonCollection] = {}
        restored_operations = 0
        for _segment, raw in operations:
            collection = raw.get("collection")
            if not isinstance(collection, dict):
                raise RestoreError("operation missing collection metadata")
            old_collection_id = int(collection["id"])
            coll = collection_map.get(old_collection_id)
            if coll is None:
                coll = await _ensure_collection(
                    sess, target_owner_user_id, database.id, collection
                )
                collection_map[old_collection_id] = coll
            await _replay_operation(sess, coll, raw)
            restored_operations += 1

        if dry_run:
            await sess.rollback()
            return RestoreResult(
                segments=len(segments),
                operations=restored_operations,
                first_operation_id=first_id,
                last_operation_id=last_id,
                dry_run=True,
            )

        for segment in segments:
            await _record_restored_segment(
                sess, segment, target_owner_user_id, database.id
            )
        state = (
            await sess.execute(
                sa.select(JsonBackupState).where(
                    JsonBackupState.database_id == database.id
                )
            )
        ).scalar_one_or_none()
        if state is None:
            sess.add(
                JsonBackupState(
                    database_id=database.id,
                    owner_user_id=target_owner_user_id,
                    last_operation_id=last_id,
                )
            )
        else:
            state.last_operation_id = max(state.last_operation_id, last_id)
            state.updated_at = sa.func.now()
        await sess.commit()

    return RestoreResult(
        segments=len(segments),
        operations=len(operations),
        first_operation_id=first_id,
        last_operation_id=last_id,
        dry_run=False,
    )


async def _ensure_collection(
    sess: Any,
    target_owner_user_id: int,
    database_id: int,
    collection: dict[str, Any],
) -> JsonCollection:
    name = str(collection["name"])
    row = (
        await sess.execute(
            sa.select(JsonCollection).where(
                JsonCollection.owner_user_id == target_owner_user_id,
                JsonCollection.database_id == database_id,
                JsonCollection.name == name,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = JsonCollection(
            database_id=database_id,
            owner_user_id=target_owner_user_id,
            name=name,
        )
        sess.add(row)
        await sess.flush()
    row.read_rule = str(collection.get("read_rule") or "owner")
    row.write_rule = str(collection.get("write_rule") or "owner")
    validator = collection.get("write_validator_json")
    row.write_validator_json = str(validator) if validator is not None else None
    return cast(JsonCollection, row)


async def _replay_operation(sess: Any, coll: JsonCollection, raw: dict[str, Any]) -> None:
    operation_id = int(raw["id"])
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        raise RestoreError(f"operation {operation_id} missing payload")
    op = str(raw["op"])
    doc_id = raw.get("doc_id")

    if op == "collection":
        action = payload.get("action")
        if action == "create":
            coll.updated_at = _parse_dt(raw.get("created_at")) or sa.func.now()
    elif op == "rules":
        coll.read_rule = str(payload.get("read", coll.read_rule))
        coll.write_rule = str(payload.get("write", coll.write_rule))
    elif op == "validator":
        validator = payload.get("validator")
        coll.write_validator_json = _json_dumps(validator) if validator is not None else None
    elif op in {"create", "set", "patch", "delete"}:
        if not isinstance(doc_id, str):
            raise RestoreError(f"operation {operation_id} missing doc_id")
        await _replay_document_operation(
            sess,
            coll,
            doc_id,
            op,
            payload,
            raw.get("created_at"),
            raw.get("owner_uid"),
        )
    else:
        raise RestoreError(f"unsupported operation {operation_id}: {op}")

    existing_op = (
        await sess.execute(sa.select(JsonOperation).where(JsonOperation.id == operation_id))
    ).scalar_one_or_none()
    if existing_op is None:
        sess.add(
            JsonOperation(
                id=operation_id,
                collection_id=coll.id,
                doc_id=doc_id if isinstance(doc_id, str) else None,
                owner_uid=raw.get("owner_uid") if isinstance(raw.get("owner_uid"), str) else None,
                op=op,
                payload_json=_json_dumps(payload),
                created_at=_parse_dt(raw.get("created_at")) or sa.func.now(),
            )
        )
    elif existing_op.op != op or existing_op.doc_id != (doc_id if isinstance(doc_id, str) else None):
        raise RestoreError(f"local operation id conflict: {operation_id}")


async def _replay_document_operation(
    sess: Any,
    coll: JsonCollection,
    doc_id: str,
    op: str,
    payload: dict[str, Any],
    created_at: Any,
    owner_uid: Any,
) -> None:
    row = (
        await sess.execute(
            sa.select(JsonDocument).where(
                JsonDocument.collection_id == coll.id,
                JsonDocument.doc_id == doc_id,
            )
        )
    ).scalar_one_or_none()
    now = _parse_dt(created_at) or sa.func.now()
    if op in {"create", "set"}:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RestoreError(f"{op} operation for {doc_id} missing object data")
        if row is None:
            row = JsonDocument(
                collection_id=coll.id,
                doc_id=doc_id,
                owner_uid=owner_uid if isinstance(owner_uid, str) else None,
                data_json=_json_dumps(data),
                version=1,
                updated_at=now,
                deleted_at=None,
            )
            sess.add(row)
        else:
            if row.owner_uid is None and isinstance(owner_uid, str):
                row.owner_uid = owner_uid
            row.data_json = _json_dumps(data)
            row.version += 1
            row.updated_at = now
            row.deleted_at = None
    elif op == "patch":
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RestoreError(f"patch operation for {doc_id} missing object data")
        if row is None:
            raise RestoreError(f"patch operation for missing document {doc_id}")
        merged = json.loads(row.data_json)
        if not isinstance(merged, dict):
            raise RestoreError(f"stored document {doc_id} is not object")
        merged.update(data)
        row.data_json = _json_dumps(merged)
        row.version += 1
        row.updated_at = now
    elif op == "delete":
        if row is None:
            raise RestoreError(f"delete operation for missing document {doc_id}")
        row.version += 1
        row.updated_at = now
        row.deleted_at = now
    coll.updated_at = now


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def _record_restored_segment(
    sess: Any,
    segment: TelegramBackupSegment,
    target_owner_user_id: int,
    database_id: int,
) -> None:
    existing = (
        await sess.execute(
            sa.select(JsonBackupSegment).where(
                JsonBackupSegment.database_id == database_id,
                JsonBackupSegment.first_operation_id == segment.first_operation_id,
                JsonBackupSegment.last_operation_id == segment.last_operation_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    sess.add(
        JsonBackupSegment(
            database_id=database_id,
            owner_user_id=target_owner_user_id,
            first_operation_id=segment.first_operation_id,
            last_operation_id=segment.last_operation_id,
            operation_count=segment.operation_count,
            telegram_chat="me",
            telegram_message_id=segment.message_id,
            sha256=segment.sha256,
            size_bytes=len(segment.compressed),
            status="restored",
            attempts=1,
            uploaded_at=sa.func.now(),
        )
    )


async def _amain(args: argparse.Namespace) -> int:
    settings = get_settings()
    settings.ensure_runtime_dirs()
    init_engine(settings)
    await run_migrations(settings)
    manager = UserbotManager(settings)
    await manager.start()
    try:
        if not await manager.is_admin_authorized():
            raise RestoreError("Telegram userbot is not authorized; restore needs Saved Messages access")
        result = await restore_json_db_from_telegram(
            manager.client,
            target_owner_user_id=args.target_user_id,
            entity=args.chat_id if args.chat_id is not None else "me",
            source_owner_user_id=args.source_user_id,
            source_database_id=args.source_database_id,
            dry_run=args.dry_run,
            limit=args.limit,
        )
        print(
            _json_dumps(
                {
                    "segments": result.segments,
                    "operations": result.operations,
                    "first_operation_id": result.first_operation_id,
                    "last_operation_id": result.last_operation_id,
                    "dry_run": result.dry_run,
                }
            )
        )
        return 0
    finally:
        await manager.stop()
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore LCloud JSON DB from Telegram LCDB1 segments")
    parser.add_argument("--target-user-id", type=int, required=True)
    parser.add_argument("--source-user-id", type=int, default=None)
    parser.add_argument("--source-database-id", type=int, default=None)
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_amain(args)))
    except RestoreError as exc:
        raise SystemExit(f"restore failed: {exc}") from exc


__all__ = [
    "RestoreError",
    "RestoreResult",
    "TelegramBackupSegment",
    "discover_lcdb1_segments",
    "main",
    "parse_lcdb1_caption",
    "restore_json_db_from_telegram",
    "restore_json_db_segments",
]


if __name__ == "__main__":
    main()
