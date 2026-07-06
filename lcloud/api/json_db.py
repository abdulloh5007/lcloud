"""LCloud DB: small JSON document database API.

This is the first public shape of "Telegram-backed DB" support. The current
implementation uses SQLite as the materialized index/query layer and writes an
append-only operation log. That log is intentionally JSONL-friendly so a later
worker can flush segments/snapshots to Telegram without changing the API.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from lcloud import __version__
from lcloud.api.app_auth import (
    ACCESS_TOKEN_TTL_SECONDS,
    AUTH_RATE_LIMIT,
    AUTH_RATE_WINDOW_SECONDS,
    REFRESH_TOKEN_TTL_DAYS,
    OptionalPublicPrincipal,
    PublicPrincipal,
)
from lcloud.api.json_databases import DatabaseKeyQuery, resolve_database
from lcloud.api.storage_public import (
    MAX_STORAGE_PUBLIC_KEYS_PER_USER,
    PUBLIC_STORAGE_RATE_WINDOW_SECONDS,
    PUBLIC_STORAGE_READ_RATE_LIMIT,
    PUBLIC_STORAGE_WRITE_RATE_LIMIT,
    STORAGE_PUBLIC_KEY_PREFIX,
)
from lcloud.auth.v2_deps import CurrentUser, OptionalCurrentUser
from lcloud.cache import (
    JSON_DOCUMENT_TTL,
    JSON_META_TTL,
    JSON_QUERY_TTL,
    PUBLIC_KEY_TTL,
    cache,
    invalidate_json_collection,
    invalidate_json_document,
    invalidate_json_public_keys,
    k_json_document,
    k_json_list,
    k_json_meta,
    k_json_public_key,
    k_json_query,
)
from lcloud.config import get_settings
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import (
    JsonCollection,
    JsonDatabase,
    JsonDbPublicKey,
    JsonDocument,
    JsonOperation,
)
from lcloud.userbot.db_backup import get_json_db_backup_status
from lcloud.utils.rate_limit import RateLimiter

router = APIRouter(prefix="/api/v1/db", tags=["json_db"])
public_router = APIRouter(prefix="/api/v1/public/db", tags=["json_db_public"])

MAX_COLLECTION_NAME_LENGTH = 64
MAX_DOCUMENT_ID_LENGTH = 128
MAX_DOCUMENT_LIST_LIMIT = 500
MAX_QUERY_FILTERS = 20
MAX_BATCH_WRITES = 100
MAX_QUERY_FIELD_PATH_LENGTH = 128
DEFAULT_PAGE_LIMIT = 50
MAX_API_KEYS_PER_USER = 25
MAX_DB_PUBLIC_KEYS_PER_USER = 25
MAX_VALIDATOR_BYTES = 1024 * 1024
PUBLIC_READ_RATE_LIMIT = 120
PUBLIC_WRITE_RATE_LIMIT = 30
PUBLIC_RATE_WINDOW_SECONDS = 60
EVENT_STREAM_POLL_SECONDS = 1.0
EVENT_STREAM_BATCH_LIMIT = 100

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
RESERVED_COLLECTIONS = {"collections"}
AccessRule = Literal["owner", "document_owner", "authenticated", "public"]
DatabaseQuery = Annotated[int | None, Query(ge=1)]
ACCESS_RULES = ("owner", "document_owner", "authenticated", "public")
DB_PUBLIC_KEY_PREFIX = "lcpk_"
DB_PUBLIC_KEY_PREFIX_LEN = len(DB_PUBLIC_KEY_PREFIX) + 8
DB_PUBLIC_KEY_ENTROPY_LEN = 32
DB_PUBLIC_KEY_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
_public_read_rate = RateLimiter(
    capacity=PUBLIC_READ_RATE_LIMIT, refill_seconds=PUBLIC_RATE_WINDOW_SECONDS
)
_public_write_rate = RateLimiter(
    capacity=PUBLIC_WRITE_RATE_LIMIT, refill_seconds=PUBLIC_RATE_WINDOW_SECONDS
)


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    database_id: int | None = Field(default=None, ge=1)
    database_key: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^lcdb_[a-z2-9]{24}$"
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = value.strip()
        if name in RESERVED_COLLECTIONS or not NAME_RE.match(name):
            raise ValueError("invalid_collection_name")
        return name


class PublicKeyIn(BaseModel):
    label: str = Field(default="", max_length=64)
    database_id: int | None = Field(default=None, ge=1)
    database_key: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^lcdb_[a-z2-9]{24}$"
    )


class CollectionRulesIn(BaseModel):
    read: AccessRule = "owner"
    write: AccessRule = "owner"


class WriteValidatorIn(BaseModel):
    max_bytes: int | None = Field(default=None, ge=1, le=MAX_VALIDATOR_BYTES)
    max_fields: int | None = Field(default=None, ge=1, le=200)
    required_fields: list[str] = Field(default_factory=list, max_length=100)
    allowed_fields: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("required_fields", "allowed_fields")
    @classmethod
    def validate_fields(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            field = value.strip()
            if not field or len(field) > MAX_QUERY_FIELD_PATH_LENGTH:
                raise ValueError("invalid_field_name")
            if "." in field:
                raise ValueError("validator_fields_are_top_level_only")
            if field not in seen:
                cleaned.append(field)
                seen.add(field)
        return cleaned

    @model_validator(mode="after")
    def validate_validator(self) -> WriteValidatorIn:
        allowed = set(self.allowed_fields)
        required = set(self.required_fields)
        if allowed and not required.issubset(allowed):
            raise ValueError("required_fields_must_be_allowed")
        return self


class CreateDocIn(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=128)
    data: dict[str, Any]

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        doc_id = value.strip()
        if not DOC_ID_RE.match(doc_id):
            raise ValueError("invalid_document_id")
        return doc_id


class SetDocIn(BaseModel):
    data: dict[str, Any]


class PatchDocIn(BaseModel):
    data: dict[str, Any]


BatchOp = Literal["create", "set", "update", "delete"]


class BatchWriteIn(BaseModel):
    op: BatchOp
    id: str | None = Field(default=None, min_length=1, max_length=128)
    data: dict[str, Any] | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        doc_id = value.strip()
        if not DOC_ID_RE.match(doc_id):
            raise ValueError("invalid_document_id")
        return doc_id

    @model_validator(mode="after")
    def validate_write(self) -> BatchWriteIn:
        if self.op in {"set", "update", "delete"} and self.id is None:
            raise ValueError("document_id_required")
        if self.op in {"create", "set", "update"} and self.data is None:
            raise ValueError("document_data_required")
        return self


class BatchIn(BaseModel):
    writes: list[BatchWriteIn] = Field(min_length=1, max_length=MAX_BATCH_WRITES)


WhereOp = Literal["==", "!=", "<", "<=", ">", ">=", "contains", "startsWith"]


class WhereIn(BaseModel):
    field: str = Field(min_length=1, max_length=128)
    op: WhereOp = "=="
    value: Any


class QueryIn(BaseModel):
    where: list[WhereIn] = Field(default_factory=list, max_length=MAX_QUERY_FILTERS)
    order_by: str | None = Field(default=None, max_length=128)
    order: Literal["asc", "desc"] = "asc"
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_DOCUMENT_LIST_LIMIT)
    offset: int = Field(default=0, ge=0)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(raw: str) -> dict[str, Any]:
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("stored JSON document is not an object")
    return loaded


def _now() -> datetime:
    return datetime.now(UTC)


def _new_doc_id() -> str:
    return f"doc_{secrets.token_urlsafe(12).replace('-', '').replace('_', '')[:16]}"


def _new_db_public_key() -> str:
    body = "".join(
        secrets.choice(DB_PUBLIC_KEY_ALPHABET)
        for _ in range(DB_PUBLIC_KEY_ENTROPY_LEN)
    )
    return f"{DB_PUBLIC_KEY_PREFIX}{body}"


def _validate_collection_name(collection: str) -> str:
    if collection in RESERVED_COLLECTIONS or not NAME_RE.match(collection):
        raise HTTPException(422, detail={"reason": "invalid_collection_name"})
    return collection


def _validate_doc_id(doc_id: str) -> str:
    if not DOC_ID_RE.match(doc_id):
        raise HTTPException(422, detail={"reason": "invalid_document_id"})
    return doc_id


def _serialize_collection(row: JsonCollection) -> dict[str, Any]:
    return {
        "id": row.id,
        "database_id": row.database_id,
        "name": row.name,
        "owner_user_id": row.owner_user_id,
        "read_rule": row.read_rule,
        "write_rule": row.write_rule,
        "write_validator": _load_write_validator(row),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_public_key(row: JsonDbPublicKey) -> dict[str, Any]:
    return {
        "id": row.id,
        "database_id": row.database_id,
        "key": row.key,
        "prefix": row.prefix,
        "label": row.label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


def _serialize_document(row: JsonDocument) -> dict[str, Any]:
    return {
        "id": row.doc_id,
        "collection_id": row.collection_id,
        "owner_id": row.owner_uid,
        "data": _json_loads(row.data_json),
        "version": row.version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_operation(row: JsonOperation) -> dict[str, Any]:
    payload = json.loads(row.payload_json)
    return {
        "id": row.id,
        "collection_id": row.collection_id,
        "doc_id": row.doc_id,
        "owner_id": row.owner_uid,
        "op": row.op,
        "payload": payload,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _sse_frame(*, event: str, data: dict[str, Any], event_id: int | None = None) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    for line in _json_dumps(data).splitlines():
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def _get_field(data: dict[str, Any], field_path: str) -> Any:
    current: Any = data
    for part in field_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _compare(left: Any, op: WhereOp, right: Any) -> bool:
    if op == "==":
        return bool(left == right)
    if op == "!=":
        return bool(left != right)
    if op == "contains":
        if isinstance(left, str):
            return str(right) in left
        if isinstance(left, list):
            return right in left
        return False
    if op == "startsWith":
        return isinstance(left, str) and left.startswith(str(right))
    if left is None:
        return False
    try:
        if op == "<":
            return bool(left < right)
        if op == "<=":
            return bool(left <= right)
        if op == ">":
            return bool(left > right)
        if op == ">=":
            return bool(left >= right)
    except TypeError:
        return False
    return False


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_public_rate(request: Request, *, action: Literal["read", "write"]) -> None:
    limiter = _public_read_rate if action == "read" else _public_write_rate
    limit = PUBLIC_READ_RATE_LIMIT if action == "read" else PUBLIC_WRITE_RATE_LIMIT
    if not limiter.try_acquire(f"{action}:{_client_ip(request)}"):
        raise HTTPException(
            429,
            detail={
                "reason": "rate_limited",
                "scope": f"public_{action}",
                "limit": limit,
                "window_seconds": PUBLIC_RATE_WINDOW_SECONDS,
            },
        )


def reset_json_db_public_rate_limits() -> None:
    _public_read_rate.reset()
    _public_write_rate.reset()


def _load_write_validator(coll: JsonCollection) -> dict[str, Any] | None:
    raw = getattr(coll, "write_validator_json", None)
    if not raw:
        return None
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        return None
    return loaded


def _validate_public_write(coll: JsonCollection, data: dict[str, Any]) -> None:
    validator = _load_write_validator(coll)
    if validator is None:
        return

    max_bytes = validator.get("max_bytes")
    if isinstance(max_bytes, int):
        size = len(_json_dumps(data).encode("utf-8"))
        if size > max_bytes:
            raise HTTPException(
                422,
                detail={
                    "reason": "public_validator_failed",
                    "check": "max_bytes",
                    "size": size,
                    "limit": max_bytes,
                },
            )

    max_fields = validator.get("max_fields")
    if isinstance(max_fields, int) and len(data) > max_fields:
        raise HTTPException(
            422,
            detail={
                "reason": "public_validator_failed",
                "check": "max_fields",
                "size": len(data),
                "limit": max_fields,
            },
        )

    required = validator.get("required_fields")
    if isinstance(required, list):
        missing = sorted(field for field in required if isinstance(field, str) and field not in data)
        if missing:
            raise HTTPException(
                422,
                detail={
                    "reason": "public_validator_failed",
                    "check": "required_fields",
                    "missing": missing,
                },
            )

    allowed = validator.get("allowed_fields")
    if isinstance(allowed, list) and allowed:
        allowed_set = {field for field in allowed if isinstance(field, str)}
        extra = sorted(set(data) - allowed_set)
        if extra:
            raise HTTPException(
                422,
                detail={
                    "reason": "public_validator_failed",
                    "check": "allowed_fields",
                    "extra": extra,
                },
            )


async def _get_collection_or_404(
    sess: AsyncSession,
    *,
    user_id: int,
    database_id: int | None,
    database_key: str | None = None,
    name: str,
) -> JsonCollection:
    query = sa.select(JsonCollection).where(
        JsonCollection.owner_user_id == user_id,
        JsonCollection.name == name,
    )
    if database_key is not None:
        query = query.join(
            JsonDatabase, JsonDatabase.id == JsonCollection.database_id
        ).where(JsonDatabase.database_key == database_key)
        if database_id is not None:
            query = query.where(JsonCollection.database_id == database_id)
    elif database_id is None:
        default_database = (
            sa.select(JsonDatabase.id)
            .where(JsonDatabase.owner_user_id == user_id)
            .order_by(JsonDatabase.is_default.desc(), JsonDatabase.id.asc())
            .limit(1)
            .scalar_subquery()
        )
        query = query.where(JsonCollection.database_id == default_database)
    else:
        query = query.where(JsonCollection.database_id == database_id)
    collection = (await sess.execute(query)).scalar_one_or_none()
    if collection is None:
        raise HTTPException(404, detail={"reason": "collection_not_found"})
    return collection


async def _get_collection_by_id_or_404(
    sess: AsyncSession,
    *,
    collection_id: int,
) -> JsonCollection:
    collection = (
        await sess.execute(
            sa.select(JsonCollection).where(JsonCollection.id == collection_id)
        )
    ).scalar_one_or_none()
    if collection is None:
        raise HTTPException(404, detail={"reason": "collection_not_found"})
    return collection


async def _get_collection_by_public_key_or_404(
    sess: AsyncSession,
    *,
    key: str,
    name: str,
) -> JsonCollection:
    key_cache = await cache.get(k_json_public_key(key))
    database_id: int | None = None
    if isinstance(key_cache, dict) and isinstance(key_cache.get("database_id"), int):
        database_id = int(key_cache["database_id"])
    if database_id is None:
        public_key = (
            await sess.execute(
                sa.select(JsonDbPublicKey).where(
                    JsonDbPublicKey.key == key,
                    JsonDbPublicKey.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if public_key is None:
            raise HTTPException(404, detail={"reason": "public_key_not_found"})
        database_id = public_key.database_id
        await cache.set(
            k_json_public_key(key),
            {"database_id": database_id},
            ttl=PUBLIC_KEY_TTL,
            namespace="json_public_key",
        )

    collection = (
        await sess.execute(
            sa.select(JsonCollection).where(
                JsonCollection.database_id == database_id,
                JsonCollection.name == name,
            )
        )
    ).scalar_one_or_none()
    if collection is None:
        raise HTTPException(404, detail={"reason": "collection_not_found"})
    return collection


def _can_access_collection(
    coll: JsonCollection,
    *,
    user: Any | None,
    action: Literal["read", "write"],
    document: JsonDocument | None = None,
) -> bool:
    owner_user = user.owner_user if isinstance(user, PublicPrincipal) else user
    app_user = user.app_user if isinstance(user, PublicPrincipal) else None
    if owner_user is not None and coll.owner_user_id == owner_user.id:
        return True
    rule = coll.read_rule if action == "read" else coll.write_rule
    if rule == "public":
        return True
    if rule == "authenticated":
        return bool(
            owner_user is not None
            or (
                app_user is not None
                and app_user.project_owner_user_id == coll.owner_user_id
                and app_user.database_id == coll.database_id
            )
        )
    if rule == "document_owner":
        if (
            app_user is None
            or app_user.project_owner_user_id != coll.owner_user_id
            or app_user.database_id != coll.database_id
        ):
            return False
        return document is None or document.owner_uid == app_user.uid
    return False


def _require_collection_access(
    coll: JsonCollection,
    *,
    user: Any | None,
    action: Literal["read", "write"],
    document: JsonDocument | None = None,
) -> None:
    if not _can_access_collection(coll, user=user, action=action, document=document):
        raise HTTPException(403, detail={"reason": "access_denied"})


async def _get_document_or_404(
    sess: AsyncSession,
    *,
    collection_id: int,
    doc_id: str,
) -> JsonDocument:
    row = (
        await sess.execute(
            sa.select(JsonDocument).where(
                JsonDocument.collection_id == collection_id,
                JsonDocument.doc_id == doc_id,
                JsonDocument.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"reason": "document_not_found"})
    return row


async def _find_document(
    sess: AsyncSession,
    *,
    collection_id: int,
    doc_id: str,
) -> JsonDocument | None:
    return (
        await sess.execute(
            sa.select(JsonDocument).where(
                JsonDocument.collection_id == collection_id,
                JsonDocument.doc_id == doc_id,
            )
        )
    ).scalar_one_or_none()


def _operation(
    collection_id: int,
    doc_id: str | None,
    op: str,
    payload: dict[str, Any],
    owner_uid: str | None = None,
) -> JsonOperation:
    return JsonOperation(
        collection_id=collection_id,
        doc_id=doc_id,
        op=op,
        payload_json=_json_dumps(payload),
        owner_uid=owner_uid,
    )


async def _stream_collection_events(
    *,
    collection_id: int,
    request: Request,
    user: Any | None,
    since: int,
    once: bool,
) -> AsyncIterator[str]:
    last_id = since
    while True:
        sm = get_sessionmaker()
        async with sm() as sess:
            coll = await _get_collection_by_id_or_404(sess, collection_id=collection_id)
            _require_collection_access(coll, user=user, action="read")
            query = sa.select(JsonOperation).where(
                JsonOperation.collection_id == collection_id,
                JsonOperation.id > last_id,
            )
            if coll.read_rule == "document_owner" and isinstance(user, PublicPrincipal):
                app_user = user.app_user
                if app_user is not None:
                    query = query.where(JsonOperation.owner_uid == app_user.uid)
            rows = (
                await sess.execute(
                    query.order_by(JsonOperation.id.asc()).limit(EVENT_STREAM_BATCH_LIMIT)
                )
            ).scalars().all()

        if rows:
            for row in rows:
                last_id = row.id
                yield _sse_frame(
                    event="lcloud.db.change",
                    data=_serialize_operation(row),
                    event_id=row.id,
                )
            if once:
                return
        elif once:
            return
        else:
            yield ": keepalive\n\n"

        if await request.is_disconnected():
            return
        await asyncio.sleep(EVENT_STREAM_POLL_SECONDS)


def _event_stream_response(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@public_router.get(
    "/key/{publishable_key}/{collection}",
    summary="Publishable-key list JSON documents by collection name",
)
async def public_key_list_documents(
    publishable_key: str,
    collection: str,
    request: Request,
    user: OptionalPublicPrincipal,
    limit: int = Query(default=50, ge=1, le=MAX_DOCUMENT_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _enforce_public_rate(request, action="read")
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_public_key_or_404(
            sess, key=publishable_key, name=name
        )
        _require_collection_access(coll, user=user, action="read")
        base = sa.select(JsonDocument).where(
            JsonDocument.collection_id == coll.id,
            JsonDocument.deleted_at.is_(None),
        )
        if coll.read_rule == "document_owner" and user.app_user is not None:
            base = base.where(JsonDocument.owner_uid == user.app_user.uid)
        total = (
            await sess.execute(sa.select(sa.func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = (
            await sess.execute(
                base.order_by(JsonDocument.updated_at.desc(), JsonDocument.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    return {
        "items": [_serialize_document(row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@public_router.post(
    "/key/{publishable_key}/{collection}",
    status_code=201,
    summary="Publishable-key create JSON document by collection name",
)
async def public_key_create_document(
    publishable_key: str,
    collection: str,
    body: CreateDocIn,
    request: Request,
    user: OptionalPublicPrincipal,
) -> dict[str, Any]:
    _enforce_public_rate(request, action="write")
    name = _validate_collection_name(collection)
    doc_id = _validate_doc_id(body.id or _new_doc_id())
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_public_key_or_404(
            sess, key=publishable_key, name=name
        )
        _require_collection_access(coll, user=user, action="write")
        _validate_public_write(coll, body.data)
        existing = await _find_document(sess, collection_id=coll.id, doc_id=doc_id)
        if existing is not None and existing.deleted_at is None:
            raise HTTPException(409, detail={"reason": "document_exists"})

        now = _now()
        if existing is None:
            row = JsonDocument(
                collection_id=coll.id,
                doc_id=doc_id,
                owner_uid=user.app_user.uid if user.app_user else None,
                data_json=_json_dumps(body.data),
                version=1,
                updated_at=now,
            )
            sess.add(row)
        else:
            _require_collection_access(coll, user=user, action="write", document=existing)
            row = existing
            row.data_json = _json_dumps(body.data)
            row.version += 1
            row.updated_at = now
            row.deleted_at = None
        coll.updated_at = now
        await sess.flush()
        sess.add(
            _operation(
                coll.id,
                doc_id,
                "create",
                {"data": body.data},
                owner_uid=row.owner_uid,
            )
        )
        await sess.commit()
        await sess.refresh(row)
        return _serialize_document(row)


@public_router.post(
    "/key/{publishable_key}/{collection}/query",
    summary="Publishable-key query JSON documents by collection name",
)
async def public_key_query_documents(
    publishable_key: str,
    collection: str,
    body: QueryIn,
    request: Request,
    user: OptionalPublicPrincipal,
) -> dict[str, Any]:
    _enforce_public_rate(request, action="read")
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_public_key_or_404(
            sess, key=publishable_key, name=name
        )
        _require_collection_access(coll, user=user, action="read")
        query = sa.select(JsonDocument).where(
            JsonDocument.collection_id == coll.id,
            JsonDocument.deleted_at.is_(None),
        )
        if coll.read_rule == "document_owner" and user.app_user is not None:
            query = query.where(JsonDocument.owner_uid == user.app_user.uid)
        rows = (
            await sess.execute(
                query.order_by(JsonDocument.updated_at.desc(), JsonDocument.id.desc())
            )
        ).scalars().all()

    matched: list[tuple[JsonDocument, dict[str, Any]]] = []
    for row in rows:
        data = _json_loads(row.data_json)
        if all(_compare(_get_field(data, w.field), w.op, w.value) for w in body.where):
            matched.append((row, data))

    if body.order_by is not None:
        matched.sort(
            key=lambda item: (
                _get_field(item[1], body.order_by or "") is None,
                _get_field(item[1], body.order_by or ""),
            ),
            reverse=body.order == "desc",
        )

    page = matched[body.offset : body.offset + body.limit]
    return {
        "items": [
            {
                "id": row.doc_id,
                "collection_id": row.collection_id,
                "owner_id": row.owner_uid,
                "data": data,
                "version": row.version,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row, data in page
        ],
        "total": len(matched),
        "limit": body.limit,
        "offset": body.offset,
    }


@public_router.get(
    "/key/{publishable_key}/{collection}/events",
    summary="Publishable-key stream JSON DB changes by collection name",
)
async def public_key_stream_collection_events(
    publishable_key: str,
    collection: str,
    request: Request,
    user: OptionalPublicPrincipal,
    since: int = Query(default=0, ge=0),
    once: bool = Query(default=False),
) -> StreamingResponse:
    _enforce_public_rate(request, action="read")
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_public_key_or_404(
            sess, key=publishable_key, name=name
        )
        _require_collection_access(coll, user=user, action="read")
        collection_id = coll.id
    return _event_stream_response(
        _stream_collection_events(
            collection_id=collection_id,
            request=request,
            user=user,
            since=since,
            once=once,
        )
    )


@public_router.get(
    "/key/{publishable_key}/{collection}/{doc_id}",
    summary="Publishable-key get JSON document by collection name",
)
async def public_key_get_document(
    publishable_key: str,
    collection: str,
    doc_id: str,
    request: Request,
    user: OptionalPublicPrincipal,
) -> dict[str, Any]:
    _enforce_public_rate(request, action="read")
    name = _validate_collection_name(collection)
    doc_id = _validate_doc_id(doc_id)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_public_key_or_404(
            sess, key=publishable_key, name=name
        )
        row = await _get_document_or_404(sess, collection_id=coll.id, doc_id=doc_id)
        _require_collection_access(coll, user=user, action="read", document=row)
    return _serialize_document(row)


@public_router.put(
    "/key/{publishable_key}/{collection}/{doc_id}",
    summary="Publishable-key replace JSON document by collection name",
)
async def public_key_set_document(
    publishable_key: str,
    collection: str,
    doc_id: str,
    body: SetDocIn,
    request: Request,
    user: OptionalPublicPrincipal,
) -> dict[str, Any]:
    _enforce_public_rate(request, action="write")
    name = _validate_collection_name(collection)
    doc_id = _validate_doc_id(doc_id)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_public_key_or_404(
            sess, key=publishable_key, name=name
        )
        _require_collection_access(coll, user=user, action="write")
        _validate_public_write(coll, body.data)
        row = await _find_document(sess, collection_id=coll.id, doc_id=doc_id)
        if row is None:
            row = JsonDocument(
                collection_id=coll.id,
                doc_id=doc_id,
                owner_uid=user.app_user.uid if user.app_user else None,
                data_json=_json_dumps(body.data),
                version=1,
                updated_at=now,
                deleted_at=None,
            )
            sess.add(row)
            op = "create"
        else:
            _require_collection_access(coll, user=user, action="write", document=row)
            row.data_json = _json_dumps(body.data)
            row.version += 1
            row.updated_at = now
            row.deleted_at = None
            op = "set"
        coll.updated_at = now
        sess.add(
            _operation(
                coll.id,
                doc_id,
                op,
                {"data": body.data},
                owner_uid=row.owner_uid,
            )
        )
        await sess.commit()
        await sess.refresh(row)
        return _serialize_document(row)


@public_router.patch(
    "/key/{publishable_key}/{collection}/{doc_id}",
    summary="Publishable-key patch JSON document by collection name",
)
async def public_key_patch_document(
    publishable_key: str,
    collection: str,
    doc_id: str,
    body: PatchDocIn,
    request: Request,
    user: OptionalPublicPrincipal,
) -> dict[str, Any]:
    _enforce_public_rate(request, action="write")
    name = _validate_collection_name(collection)
    doc_id = _validate_doc_id(doc_id)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_public_key_or_404(
            sess, key=publishable_key, name=name
        )
        _require_collection_access(coll, user=user, action="write")
        row = await _get_document_or_404(sess, collection_id=coll.id, doc_id=doc_id)
        _require_collection_access(coll, user=user, action="write", document=row)
        data = _json_loads(row.data_json)
        data.update(body.data)
        _validate_public_write(coll, data)
        row.data_json = _json_dumps(data)
        row.version += 1
        row.updated_at = now
        coll.updated_at = now
        sess.add(
            _operation(
                coll.id,
                doc_id,
                "patch",
                {"data": body.data},
                owner_uid=row.owner_uid,
            )
        )
        await sess.commit()
        await sess.refresh(row)
        return _serialize_document(row)


@public_router.delete(
    "/key/{publishable_key}/{collection}/{doc_id}",
    status_code=204,
    response_class=Response,
    summary="Publishable-key delete JSON document by collection name",
)
async def public_key_delete_document(
    publishable_key: str,
    collection: str,
    doc_id: str,
    request: Request,
    user: OptionalPublicPrincipal,
) -> Response:
    _enforce_public_rate(request, action="write")
    name = _validate_collection_name(collection)
    doc_id = _validate_doc_id(doc_id)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_public_key_or_404(
            sess, key=publishable_key, name=name
        )
        _require_collection_access(coll, user=user, action="write")
        row = await _get_document_or_404(sess, collection_id=coll.id, doc_id=doc_id)
        _require_collection_access(coll, user=user, action="write", document=row)
        row.deleted_at = now
        row.version += 1
        row.updated_at = now
        coll.updated_at = now
        sess.add(_operation(coll.id, doc_id, "delete", {}, owner_uid=row.owner_uid))
        await sess.commit()
    return Response(status_code=204)


@router.get(
    "/_meta",
    summary="LCloud DB capabilities and limits",
    description=(
        "Machine-readable limits for SDKs and AI agents. Values describe the "
        "current deployed API contract; clients should stay within them."
    ),
)
async def db_meta() -> dict[str, Any]:
    cached = await cache.get(k_json_meta())
    if isinstance(cached, dict):
        return cached
    settings = get_settings()
    body = {
        "name": "LCloud DB",
        "version": __version__,
        "documents": {
            "data_type": "json_object",
            "recommended_max_size_bytes": 100 * 1024,
            "patch": "shallow_top_level_merge",
            "generated_id_prefix": "doc_",
        },
        "collections": {
            "name_regex": NAME_RE.pattern,
            "name_max_length": MAX_COLLECTION_NAME_LENGTH,
            "reserved": sorted(RESERVED_COLLECTIONS),
        },
        "databases": {
            "list_create_path": "/api/v1/db/databases",
            "scope_query_param": "database_id",
            "scope_key_query_param": "database_key",
            "database_key_prefix": "lcdb_",
            "database_key_public_resolve_path": (
                "/api/v1/public/db/databases/{database_key}"
            ),
            "database_creation_requires_owner_api": True,
            "telegram_chat_per_database": True,
            "contains": ["collections", "documents", "media", "keys", "backups"],
        },
        "document_ids": {
            "regex": DOC_ID_RE.pattern,
            "max_length": MAX_DOCUMENT_ID_LENGTH,
        },
        "pagination": {
            "default_limit": DEFAULT_PAGE_LIMIT,
            "max_limit": MAX_DOCUMENT_LIST_LIMIT,
            "offset_min": 0,
        },
        "query": {
            "max_where_filters": MAX_QUERY_FILTERS,
            "max_field_path_length": MAX_QUERY_FIELD_PATH_LENGTH,
            "operators": ["==", "!=", "<", "<=", ">", ">=", "contains", "startsWith"],
            "field_paths": "dot_notation",
            "engine": "in_process_scan_over_materialized_documents",
            "indexes": "not_user_configurable_yet",
        },
        "batch": {
            "max_writes": MAX_BATCH_WRITES,
            "operations": ["create", "set", "update", "delete"],
            "atomic": True,
        },
        "realtime": {
            "transport": "sse",
            "event": "lcloud.db.change",
            "owner_path": "/api/v1/db/{collection}/events",
            "public_path": "/api/v1/public/db/{collection_id}/events",
            "cursor": "json_operations.id",
            "query_params": ["since", "once"],
            "poll_seconds": EVENT_STREAM_POLL_SECONDS,
            "batch_limit": EVENT_STREAM_BATCH_LIMIT,
        },
        "access_rules": {
            "rules": list(ACCESS_RULES),
            "default_read": "owner",
            "default_write": "owner",
            "public_base_path": "/api/v1/public/db/{collection_id}",
            "publishable_key_path": (
                "/api/v1/public/db/key/{publishable_key}/{collection}"
            ),
            "owner_manage_path": "/api/v1/db/collections/{collection}/rules",
            "write_validator_path": "/api/v1/db/collections/{collection}/validator",
            "publishable_key_manage_path": "/api/v1/db/public-keys",
            "publishable_key_prefix": DB_PUBLIC_KEY_PREFIX,
            "publishable_key_can_create_databases": False,
            "max_publishable_keys_per_user": MAX_DB_PUBLIC_KEYS_PER_USER,
            "public_read_rate_limit": {
                "capacity": PUBLIC_READ_RATE_LIMIT,
                "window_seconds": PUBLIC_RATE_WINDOW_SECONDS,
                "key": "ip",
            },
            "public_write_rate_limit": {
                "capacity": PUBLIC_WRITE_RATE_LIMIT,
                "window_seconds": PUBLIC_RATE_WINDOW_SECONDS,
                "key": "ip",
            },
            "write_validator": {
                "max_configurable_bytes": MAX_VALIDATOR_BYTES,
                "fields": [
                    "max_bytes",
                    "max_fields",
                    "required_fields",
                    "allowed_fields",
                ],
                "scope": "public_create_set_patch",
            },
        },
        "media": {
            "max_upload_bytes": settings.lc_max_file_bytes,
            "list_max_limit": MAX_DOCUMENT_LIST_LIMIT,
            "default_compress": True,
            "lc2_client_signing": "optional_fields_supported_for_owner_api",
            "publishable_storage_key_prefix": STORAGE_PUBLIC_KEY_PREFIX,
            "publishable_storage_key_manage_path": "/api/v1/storage/public-keys",
            "publishable_storage_key_path": "/api/v1/public/storage/key/{storage_key}/files",
            "max_publishable_storage_keys_per_user": MAX_STORAGE_PUBLIC_KEYS_PER_USER,
            "public_storage_read_rate_limit": {
                "capacity": PUBLIC_STORAGE_READ_RATE_LIMIT,
                "window_seconds": PUBLIC_STORAGE_RATE_WINDOW_SECONDS,
                "key": "ip",
            },
            "public_storage_write_rate_limit": {
                "capacity": PUBLIC_STORAGE_WRITE_RATE_LIMIT,
                "window_seconds": PUBLIC_STORAGE_RATE_WINDOW_SECONDS,
                "key": "ip",
            },
        },
        "auth": {
            "methods": [
                "lc_user_session_cookie",
                "bearer_api_key",
                "anonymous_app_user",
            ],
            "max_active_api_keys_per_user": MAX_API_KEYS_PER_USER,
            "api_keys_safe_for_public_browser": False,
            "app_access_token_ttl_seconds": ACCESS_TOKEN_TTL_SECONDS,
            "app_refresh_token_sliding_ttl_days": REFRESH_TOKEN_TTL_DAYS,
            "app_auth_path": "/api/v1/public/auth/key/{publishable_key}",
            "app_auth_rate_limit": {
                "capacity": AUTH_RATE_LIMIT,
                "window_seconds": AUTH_RATE_WINDOW_SECONDS,
                "key": "ip",
            },
            "v2_login_rate_limit": {
                "capacity": 10,
                "window_seconds": 300,
                "key": "ip",
                "applies_to": ["/auth/v2/challenge", "/auth/v2/verify"],
            },
        },
        "backup": {
            "telegram_segments": True,
            "target": "database_telegram_chat",
            "format": "lcloud-json-db-segment-v1",
            "status_path": "/api/v1/db/backup/status",
            "interval_seconds": settings.lc_json_db_backup_interval_seconds,
            "batch_operations": settings.lc_json_db_backup_batch_operations,
        },
        "rate_limits": {
            "db_api": "no_explicit_per_user_rate_limit_yet",
            "storage_api": "no_explicit_http_rate_limit_yet",
            "telegram_mtproto": {
                "rate_per_second": settings.lc_mtproto_rate_per_sec,
                "burst": settings.lc_mtproto_burst,
                "max_floodwait_seconds": settings.lc_mtproto_max_floodwait_sec,
            },
        },
        "not_supported_yet": [
            "joins",
            "server_side_sql",
            "realtime_subscriptions",
            "custom_rule_expressions",
            "user_defined_indexes",
            "deep_patch_merge",
        ],
    }
    await cache.set(k_json_meta(), body, ttl=JSON_META_TTL, namespace="json_meta")
    return body


@router.get(
    "/backup/status",
    summary="JSON DB Telegram backup status",
)
async def backup_status(user: CurrentUser, database_id: DatabaseQuery = None, database_key: DatabaseKeyQuery = None) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        database = await resolve_database(sess, user=user, database_id=database_id, database_key=database_key)
    return await get_json_db_backup_status(user.id, database.id)


@router.get(
    "/collections",
    summary="List JSON DB collections",
    description="Returns collections owned by the current V2 user.",
)
async def list_collections(
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        database = await resolve_database(sess, user=user, database_id=database_id, database_key=database_key)
        rows = (
            await sess.execute(
                sa.select(JsonCollection)
                .where(
                    JsonCollection.owner_user_id == user.id,
                    JsonCollection.database_id == database.id,
                )
                .order_by(JsonCollection.updated_at.desc(), JsonCollection.id.desc())
            )
        ).scalars().all()
    return [_serialize_collection(row) for row in rows]


@router.post(
    "/collections",
    status_code=201,
    summary="Create JSON DB collection",
)
async def create_collection(
    body: CollectionIn,
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        database = await resolve_database(
            sess, user=user, database_id=body.database_id or database_id, database_key=body.database_key or database_key
        )
        existing = (
            await sess.execute(
                sa.select(JsonCollection).where(
                    JsonCollection.owner_user_id == user.id,
                    JsonCollection.database_id == database.id,
                    JsonCollection.name == body.name,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(409, detail={"reason": "collection_exists"})
        now = _now()
        row = JsonCollection(
            database_id=database.id,
            owner_user_id=user.id,
            name=body.name,
            updated_at=now,
        )
        sess.add(row)
        await sess.flush()
        sess.add(
            _operation(
                row.id,
                None,
                "collection",
                {"action": "create", "name": body.name},
            )
        )
        await sess.commit()
        await sess.refresh(row)
        await invalidate_json_collection(row.id)
        return _serialize_collection(row)


@router.get(
    "/public-keys",
    summary="List publishable DB keys",
    description=(
        "Publishable DB keys are safe to embed in frontend apps. They identify "
        "the user's public DB namespace; collection rules still control access."
    ),
)
async def list_public_keys(
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        database = await resolve_database(sess, user=user, database_id=database_id, database_key=database_key)
        rows = (
            await sess.execute(
                sa.select(JsonDbPublicKey)
                .where(
                    JsonDbPublicKey.owner_user_id == user.id,
                    JsonDbPublicKey.database_id == database.id,
                )
                .order_by(JsonDbPublicKey.created_at.desc(), JsonDbPublicKey.id.desc())
            )
        ).scalars().all()
        return [_serialize_public_key(row) for row in rows]


@router.post(
    "/public-keys",
    status_code=201,
    summary="Create publishable DB key",
)
async def create_public_key(
    body: PublicKeyIn,
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        database = await resolve_database(
            sess, user=user, database_id=body.database_id or database_id, database_key=body.database_key or database_key
        )
        active = (
            await sess.execute(
                sa.select(sa.func.count())
                .select_from(JsonDbPublicKey)
                .where(
                    JsonDbPublicKey.owner_user_id == user.id,
                    JsonDbPublicKey.database_id == database.id,
                    JsonDbPublicKey.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        if active >= MAX_DB_PUBLIC_KEYS_PER_USER:
            raise HTTPException(
                400,
                detail={
                    "reason": "public_key_limit_reached",
                    "max": MAX_DB_PUBLIC_KEYS_PER_USER,
                },
            )

        key = _new_db_public_key()
        while (
            await sess.execute(
                sa.select(JsonDbPublicKey.id).where(JsonDbPublicKey.key == key)
            )
        ).scalar_one_or_none():
            key = _new_db_public_key()

        row = JsonDbPublicKey(
            database_id=database.id,
            owner_user_id=user.id,
            key=key,
            prefix=key[:DB_PUBLIC_KEY_PREFIX_LEN],
            label=body.label.strip(),
        )
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)
        await invalidate_json_public_keys()
        return _serialize_public_key(row)


@router.delete(
    "/public-keys/{key_id}",
    summary="Revoke publishable DB key",
)
async def revoke_public_key(key_id: int, user: CurrentUser) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(JsonDbPublicKey).where(
                    JsonDbPublicKey.id == key_id,
                    JsonDbPublicKey.owner_user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "public_key_not_found"})
        if row.revoked_at is None:
            row.revoked_at = _now()
            await sess.commit()
        await invalidate_json_public_keys()
        return {"ok": True}


@router.get(
    "/collections/{collection}/rules",
    summary="Get JSON DB collection access rules",
)
async def get_collection_rules(
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        row = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        return {
            "collection": row.name,
            "collection_id": row.id,
            "read": row.read_rule,
            "write": row.write_rule,
            "public_base_path": f"/api/v1/public/db/{row.id}",
        }


@router.put(
    "/collections/{collection}/rules",
    summary="Update JSON DB collection access rules",
)
async def set_collection_rules(
    body: CollectionRulesIn,
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        row = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        row.read_rule = body.read
        row.write_rule = body.write
        row.updated_at = _now()
        sess.add(_operation(row.id, None, "rules", {"read": body.read, "write": body.write}))
        await sess.commit()
        await sess.refresh(row)
        await invalidate_json_collection(row.id)
        return {
            "collection": row.name,
            "collection_id": row.id,
            "read": row.read_rule,
            "write": row.write_rule,
            "public_base_path": f"/api/v1/public/db/{row.id}",
        }


@router.get(
    "/collections/{collection}/validator",
    summary="Get JSON DB public write validator",
)
async def get_collection_validator(
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        row = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        return {
            "collection": row.name,
            "collection_id": row.id,
            "validator": _load_write_validator(row),
        }


@router.put(
    "/collections/{collection}/validator",
    summary="Set JSON DB public write validator",
)
async def set_collection_validator(
    body: WriteValidatorIn,
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    validator = body.model_dump()
    sm = get_sessionmaker()
    async with sm() as sess:
        row = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        row.write_validator_json = _json_dumps(validator)
        row.updated_at = _now()
        sess.add(_operation(row.id, None, "validator", {"validator": validator}))
        await sess.commit()
        await sess.refresh(row)
        await invalidate_json_collection(row.id)
        return {
            "collection": row.name,
            "collection_id": row.id,
            "validator": _load_write_validator(row),
        }


@router.delete(
    "/collections/{collection}/validator",
    status_code=204,
    response_class=Response,
    summary="Clear JSON DB public write validator",
)
async def delete_collection_validator(
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
) -> Response:
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        row = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        row.write_validator_json = None
        row.updated_at = _now()
        sess.add(_operation(row.id, None, "validator", {"validator": None}))
        await sess.commit()
        await invalidate_json_collection(row.id)
    return Response(status_code=204)


@router.delete(
    "/collections/{collection}",
    status_code=204,
    response_class=Response,
    summary="Delete JSON DB collection",
)
async def delete_collection(
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
) -> Response:
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        row = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        collection_id = row.id
        await sess.delete(row)
        await sess.commit()
        await invalidate_json_collection(collection_id)
    return Response(status_code=204)


@router.get(
    "/{collection}",
    summary="List documents in a collection",
)
async def list_documents(
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        cache_key = k_json_list(coll.id, limit, offset)
        cached = await cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
        base = sa.select(JsonDocument).where(
            JsonDocument.collection_id == coll.id,
            JsonDocument.deleted_at.is_(None),
        )
        total = (
            await sess.execute(
                sa.select(sa.func.count()).select_from(base.subquery())
            )
        ).scalar_one()
        rows = (
            await sess.execute(
                base.order_by(JsonDocument.updated_at.desc(), JsonDocument.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    body = {
        "items": [_serialize_document(row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }
    await cache.set(cache_key, body, ttl=JSON_QUERY_TTL, namespace="json_list")
    return body


@router.post(
    "/{collection}",
    status_code=201,
    summary="Create JSON document",
)
async def create_document(
    body: CreateDocIn,
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    doc_id = body.id or _new_doc_id()
    _validate_doc_id(doc_id)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        existing = await _find_document(sess, collection_id=coll.id, doc_id=doc_id)
        if existing is not None and existing.deleted_at is None:
            raise HTTPException(409, detail={"reason": "document_exists"})

        now = _now()
        if existing is None:
            row = JsonDocument(
                collection_id=coll.id,
                doc_id=doc_id,
                data_json=_json_dumps(body.data),
                version=1,
                updated_at=now,
            )
            sess.add(row)
        else:
            row = existing
            row.data_json = _json_dumps(body.data)
            row.version += 1
            row.updated_at = now
            row.deleted_at = None
        coll.updated_at = now
        await sess.flush()
        sess.add(_operation(coll.id, doc_id, "create", {"data": body.data}))
        await sess.commit()
        await sess.refresh(row)
        await invalidate_json_document(coll.id, doc_id)
        return _serialize_document(row)


@router.post(
    "/{collection}/query",
    summary="Query JSON documents",
    description=(
        "MVP query engine over materialized JSON documents. Supports simple "
        "field paths like `profile.city`, equality/range/string/list filters, "
        "ordering, limit, and offset."
    ),
)
async def query_documents(
    body: QueryIn,
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        cache_key = k_json_query(coll.id, body.model_dump(mode="json"))
        cached = await cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
        rows = (
            await sess.execute(
                sa.select(JsonDocument)
                .where(
                    JsonDocument.collection_id == coll.id,
                    JsonDocument.deleted_at.is_(None),
                )
                .order_by(JsonDocument.updated_at.desc(), JsonDocument.id.desc())
            )
        ).scalars().all()

    matched: list[tuple[JsonDocument, dict[str, Any]]] = []
    for row in rows:
        data = _json_loads(row.data_json)
        if all(_compare(_get_field(data, w.field), w.op, w.value) for w in body.where):
            matched.append((row, data))

    order_by = body.order_by
    if order_by is not None:
        matched.sort(
            key=lambda item: (
                _get_field(item[1], order_by) is None,
                _get_field(item[1], order_by),
            ),
            reverse=body.order == "desc",
        )

    page = matched[body.offset : body.offset + body.limit]
    result = {
        "items": [
            {
                "id": row.doc_id,
                "collection_id": row.collection_id,
                "data": data,
                "version": row.version,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row, data in page
        ],
        "total": len(matched),
        "limit": body.limit,
        "offset": body.offset,
    }
    await cache.set(cache_key, result, ttl=JSON_QUERY_TTL, namespace="json_query")
    return result


@router.post(
    "/{collection}/batch",
    summary="Atomically write multiple JSON documents",
    description=(
        "Runs up to 100 create/set/update/delete operations in one database "
        "transaction. If one operation fails, none of the writes are committed."
    ),
)
async def batch_documents(
    body: BatchIn,
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        results: list[dict[str, Any]] = []
        changed_rows: list[JsonDocument] = []

        for index, write in enumerate(body.writes):
            doc_id = write.id or _new_doc_id()
            doc_id = _validate_doc_id(doc_id)
            data = write.data

            if write.op == "create":
                row = await _find_document(sess, collection_id=coll.id, doc_id=doc_id)
                if row is not None and row.deleted_at is None:
                    raise HTTPException(
                        409,
                        detail={
                            "reason": "document_exists",
                            "index": index,
                            "id": doc_id,
                        },
                    )
                assert data is not None
                if row is None:
                    row = JsonDocument(
                        collection_id=coll.id,
                        doc_id=doc_id,
                        data_json=_json_dumps(data),
                        version=1,
                        updated_at=now,
                    )
                    sess.add(row)
                else:
                    row.data_json = _json_dumps(data)
                    row.version += 1
                    row.updated_at = now
                    row.deleted_at = None
                sess.add(_operation(coll.id, doc_id, "create", {"data": data}))
                await sess.flush()
                changed_rows.append(row)
                results.append({"index": index, "op": write.op, "id": doc_id})
                continue

            if write.op == "set":
                assert data is not None
                row = await _find_document(sess, collection_id=coll.id, doc_id=doc_id)
                if row is None:
                    row = JsonDocument(
                        collection_id=coll.id,
                        doc_id=doc_id,
                        data_json=_json_dumps(data),
                        version=1,
                        updated_at=now,
                        deleted_at=None,
                    )
                    sess.add(row)
                    op = "create"
                else:
                    row.data_json = _json_dumps(data)
                    row.version += 1
                    row.updated_at = now
                    row.deleted_at = None
                    op = "set"
                sess.add(_operation(coll.id, doc_id, op, {"data": data}))
                await sess.flush()
                changed_rows.append(row)
                results.append({"index": index, "op": write.op, "id": doc_id})
                continue

            if write.op == "update":
                assert data is not None
                row = await _get_document_or_404(
                    sess, collection_id=coll.id, doc_id=doc_id
                )
                merged = _json_loads(row.data_json)
                merged.update(data)
                row.data_json = _json_dumps(merged)
                row.version += 1
                row.updated_at = now
                sess.add(_operation(coll.id, doc_id, "patch", {"data": data}))
                changed_rows.append(row)
                results.append({"index": index, "op": write.op, "id": doc_id})
                continue

            row = await _get_document_or_404(sess, collection_id=coll.id, doc_id=doc_id)
            row.deleted_at = now
            row.version += 1
            row.updated_at = now
            sess.add(_operation(coll.id, doc_id, "delete", {}))
            results.append({"index": index, "op": write.op, "id": doc_id})

        coll.updated_at = now
        await sess.commit()
        for row in changed_rows:
            await sess.refresh(row)
        await invalidate_json_collection(coll.id)

    serialized = {row.doc_id: _serialize_document(row) for row in changed_rows}
    return {
        "items": [
            {
                **result,
                "document": serialized.get(result["id"]),
            }
            for result in results
        ],
        "total": len(results),
    }


@router.get(
    "/{collection}/events",
    summary="Stream JSON DB collection changes as Server-Sent Events",
)
async def stream_collection_events(
    request: Request,
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
    since: int = Query(default=0, ge=0),
    once: bool = Query(default=False),
) -> StreamingResponse:
    name = _validate_collection_name(collection)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        collection_id = coll.id
    return _event_stream_response(
        _stream_collection_events(
            collection_id=collection_id,
            request=request,
            user=user,
            since=since,
            once=once,
        )
    )


@router.get(
    "/{collection}/{doc_id}",
    summary="Get JSON document",
)
async def get_document(
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
    doc_id: str = Path(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    doc_id = _validate_doc_id(doc_id)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        cache_key = k_json_document(coll.id, doc_id)
        cached = await cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
        row = await _get_document_or_404(sess, collection_id=coll.id, doc_id=doc_id)
    body = _serialize_document(row)
    await cache.set(cache_key, body, ttl=JSON_DOCUMENT_TTL, namespace="json_doc")
    return body


@router.put(
    "/{collection}/{doc_id}",
    summary="Replace JSON document",
)
async def set_document(
    body: SetDocIn,
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
    doc_id: str = Path(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    doc_id = _validate_doc_id(doc_id)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        row = (
            await sess.execute(
                sa.select(JsonDocument).where(
                    JsonDocument.collection_id == coll.id,
                    JsonDocument.doc_id == doc_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = JsonDocument(
                collection_id=coll.id,
                doc_id=doc_id,
                data_json=_json_dumps(body.data),
                version=1,
                updated_at=now,
                deleted_at=None,
            )
            sess.add(row)
            op = "create"
        else:
            row.data_json = _json_dumps(body.data)
            row.version += 1
            row.updated_at = now
            row.deleted_at = None
            op = "set"
        coll.updated_at = now
        sess.add(_operation(coll.id, doc_id, op, {"data": body.data}))
        await sess.commit()
        await sess.refresh(row)
        await invalidate_json_document(coll.id, doc_id)
        return _serialize_document(row)


@router.patch(
    "/{collection}/{doc_id}",
    summary="Patch JSON document",
)
async def patch_document(
    body: PatchDocIn,
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
    doc_id: str = Path(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    name = _validate_collection_name(collection)
    doc_id = _validate_doc_id(doc_id)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        row = await _get_document_or_404(sess, collection_id=coll.id, doc_id=doc_id)
        data = _json_loads(row.data_json)
        data.update(body.data)
        row.data_json = _json_dumps(data)
        row.version += 1
        row.updated_at = now
        coll.updated_at = now
        sess.add(_operation(coll.id, doc_id, "patch", {"data": body.data}))
        await sess.commit()
        await sess.refresh(row)
        await invalidate_json_document(coll.id, doc_id)
        return _serialize_document(row)


@router.delete(
    "/{collection}/{doc_id}",
    status_code=204,
    response_class=Response,
    summary="Delete JSON document",
)
async def delete_document(
    user: CurrentUser,
    database_id: DatabaseQuery = None,
    database_key: DatabaseKeyQuery = None,
    collection: str = Path(..., min_length=1, max_length=64),
    doc_id: str = Path(..., min_length=1, max_length=128),
) -> Response:
    name = _validate_collection_name(collection)
    doc_id = _validate_doc_id(doc_id)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_or_404(sess, user_id=user.id, database_id=database_id, database_key=database_key, name=name)
        row = await _get_document_or_404(sess, collection_id=coll.id, doc_id=doc_id)
        row.deleted_at = now
        row.version += 1
        row.updated_at = now
        coll.updated_at = now
        sess.add(_operation(coll.id, doc_id, "delete", {}))
        await sess.commit()
        await invalidate_json_document(coll.id, doc_id)
    return Response(status_code=204)


@public_router.get(
    "/{collection_id}",
    summary="Public/list-access JSON documents by collection ID",
)
async def public_list_documents(
    collection_id: int,
    request: Request,
    user: OptionalCurrentUser,
    limit: int = Query(default=50, ge=1, le=MAX_DOCUMENT_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _enforce_public_rate(request, action="read")
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_id_or_404(sess, collection_id=collection_id)
        _require_collection_access(coll, user=user, action="read")
        cache_key = k_json_list(coll.id, limit, offset)
        cached = await cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
        base = sa.select(JsonDocument).where(
            JsonDocument.collection_id == coll.id,
            JsonDocument.deleted_at.is_(None),
        )
        total = (
            await sess.execute(sa.select(sa.func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = (
            await sess.execute(
                base.order_by(JsonDocument.updated_at.desc(), JsonDocument.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    body = {
        "items": [_serialize_document(row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }
    await cache.set(cache_key, body, ttl=JSON_QUERY_TTL, namespace="json_list")
    return body


@public_router.post(
    "/{collection_id}",
    status_code=201,
    summary="Public/write-access create JSON document by collection ID",
)
async def public_create_document(
    collection_id: int,
    body: CreateDocIn,
    request: Request,
    user: OptionalCurrentUser,
) -> dict[str, Any]:
    _enforce_public_rate(request, action="write")
    doc_id = body.id or _new_doc_id()
    doc_id = _validate_doc_id(doc_id)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_id_or_404(sess, collection_id=collection_id)
        _require_collection_access(coll, user=user, action="write")
        _validate_public_write(coll, body.data)
        existing = await _find_document(sess, collection_id=coll.id, doc_id=doc_id)
        if existing is not None and existing.deleted_at is None:
            raise HTTPException(409, detail={"reason": "document_exists"})

        now = _now()
        if existing is None:
            row = JsonDocument(
                collection_id=coll.id,
                doc_id=doc_id,
                data_json=_json_dumps(body.data),
                version=1,
                updated_at=now,
            )
            sess.add(row)
        else:
            row = existing
            row.data_json = _json_dumps(body.data)
            row.version += 1
            row.updated_at = now
            row.deleted_at = None
        coll.updated_at = now
        await sess.flush()
        sess.add(_operation(coll.id, doc_id, "create", {"data": body.data}))
        await sess.commit()
        await sess.refresh(row)
        await invalidate_json_document(coll.id, doc_id)
        return _serialize_document(row)


@public_router.post(
    "/{collection_id}/query",
    summary="Public/list-access query JSON documents by collection ID",
)
async def public_query_documents(
    collection_id: int,
    body: QueryIn,
    request: Request,
    user: OptionalCurrentUser,
) -> dict[str, Any]:
    _enforce_public_rate(request, action="read")
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_id_or_404(sess, collection_id=collection_id)
        _require_collection_access(coll, user=user, action="read")
        cache_key = k_json_query(coll.id, body.model_dump(mode="json"))
        cached = await cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
        rows = (
            await sess.execute(
                sa.select(JsonDocument)
                .where(
                    JsonDocument.collection_id == coll.id,
                    JsonDocument.deleted_at.is_(None),
                )
                .order_by(JsonDocument.updated_at.desc(), JsonDocument.id.desc())
            )
        ).scalars().all()

    matched: list[tuple[JsonDocument, dict[str, Any]]] = []
    for row in rows:
        data = _json_loads(row.data_json)
        if all(_compare(_get_field(data, w.field), w.op, w.value) for w in body.where):
            matched.append((row, data))

    if body.order_by is not None:
        matched.sort(
            key=lambda item: (
                _get_field(item[1], body.order_by or "") is None,
                _get_field(item[1], body.order_by or ""),
            ),
            reverse=body.order == "desc",
        )

    page = matched[body.offset : body.offset + body.limit]
    result = {
        "items": [
            {
                "id": row.doc_id,
                "collection_id": row.collection_id,
                "data": data,
                "version": row.version,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row, data in page
        ],
        "total": len(matched),
        "limit": body.limit,
        "offset": body.offset,
    }
    await cache.set(cache_key, result, ttl=JSON_QUERY_TTL, namespace="json_query")
    return result


@public_router.get(
    "/{collection_id}/events",
    summary="Stream public/read-access JSON DB changes as Server-Sent Events",
)
async def public_stream_collection_events(
    collection_id: int,
    request: Request,
    user: OptionalCurrentUser,
    since: int = Query(default=0, ge=0),
    once: bool = Query(default=False),
) -> StreamingResponse:
    _enforce_public_rate(request, action="read")
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_id_or_404(sess, collection_id=collection_id)
        _require_collection_access(coll, user=user, action="read")
    return _event_stream_response(
        _stream_collection_events(
            collection_id=collection_id,
            request=request,
            user=user,
            since=since,
            once=once,
        )
    )


@public_router.get(
    "/{collection_id}/{doc_id}",
    summary="Public/read-access get JSON document by collection ID",
)
async def public_get_document(
    collection_id: int,
    doc_id: str,
    request: Request,
    user: OptionalCurrentUser,
) -> dict[str, Any]:
    _enforce_public_rate(request, action="read")
    doc_id = _validate_doc_id(doc_id)
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_id_or_404(sess, collection_id=collection_id)
        _require_collection_access(coll, user=user, action="read")
        cache_key = k_json_document(coll.id, doc_id)
        cached = await cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
        row = await _get_document_or_404(sess, collection_id=coll.id, doc_id=doc_id)
    body = _serialize_document(row)
    await cache.set(cache_key, body, ttl=JSON_DOCUMENT_TTL, namespace="json_doc")
    return body


@public_router.put(
    "/{collection_id}/{doc_id}",
    summary="Public/write-access replace JSON document by collection ID",
)
async def public_set_document(
    collection_id: int,
    doc_id: str,
    body: SetDocIn,
    request: Request,
    user: OptionalCurrentUser,
) -> dict[str, Any]:
    _enforce_public_rate(request, action="write")
    doc_id = _validate_doc_id(doc_id)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_id_or_404(sess, collection_id=collection_id)
        _require_collection_access(coll, user=user, action="write")
        _validate_public_write(coll, body.data)
        row = await _find_document(sess, collection_id=coll.id, doc_id=doc_id)
        if row is None:
            row = JsonDocument(
                collection_id=coll.id,
                doc_id=doc_id,
                data_json=_json_dumps(body.data),
                version=1,
                updated_at=now,
                deleted_at=None,
            )
            sess.add(row)
            op = "create"
        else:
            row.data_json = _json_dumps(body.data)
            row.version += 1
            row.updated_at = now
            row.deleted_at = None
            op = "set"
        coll.updated_at = now
        sess.add(_operation(coll.id, doc_id, op, {"data": body.data}))
        await sess.commit()
        await sess.refresh(row)
        await invalidate_json_document(coll.id, doc_id)
        return _serialize_document(row)


@public_router.patch(
    "/{collection_id}/{doc_id}",
    summary="Public/write-access patch JSON document by collection ID",
)
async def public_patch_document(
    collection_id: int,
    doc_id: str,
    body: PatchDocIn,
    request: Request,
    user: OptionalCurrentUser,
) -> dict[str, Any]:
    _enforce_public_rate(request, action="write")
    doc_id = _validate_doc_id(doc_id)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_id_or_404(sess, collection_id=collection_id)
        _require_collection_access(coll, user=user, action="write")
        row = await _get_document_or_404(sess, collection_id=coll.id, doc_id=doc_id)
        data = _json_loads(row.data_json)
        data.update(body.data)
        _validate_public_write(coll, data)
        row.data_json = _json_dumps(data)
        row.version += 1
        row.updated_at = now
        coll.updated_at = now
        sess.add(_operation(coll.id, doc_id, "patch", {"data": body.data}))
        await sess.commit()
        await sess.refresh(row)
        await invalidate_json_document(coll.id, doc_id)
        return _serialize_document(row)


@public_router.delete(
    "/{collection_id}/{doc_id}",
    status_code=204,
    response_class=Response,
    summary="Public/write-access delete JSON document by collection ID",
)
async def public_delete_document(
    collection_id: int,
    doc_id: str,
    request: Request,
    user: OptionalCurrentUser,
) -> Response:
    _enforce_public_rate(request, action="write")
    doc_id = _validate_doc_id(doc_id)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        coll = await _get_collection_by_id_or_404(sess, collection_id=collection_id)
        _require_collection_access(coll, user=user, action="write")
        row = await _get_document_or_404(sess, collection_id=coll.id, doc_id=doc_id)
        row.deleted_at = now
        row.version += 1
        row.updated_at = now
        coll.updated_at = now
        sess.add(_operation(coll.id, doc_id, "delete", {}))
        await sess.commit()
        await invalidate_json_document(coll.id, doc_id)
    return Response(status_code=204)


__all__ = ["public_router", "reset_json_db_public_rate_limits", "router"]
