"""Top-level LCloud Database projects backed by Telegram cloud chats."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.errors import RPCError

from lcloud.auth.v2_deps import CurrentUser
from lcloud.cache import (
    JSON_DB_TTL,
    cache,
    invalidate_json_database,
    invalidate_user_clouds,
    k_json_database_key,
)
from lcloud.crypto.keys import ensure_admin_keypair
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import Cloud, JsonCollection, JsonDatabase, Owner, User
from lcloud.userbot.client import UserbotManager, get_userbot_manager
from lcloud.userbot.clouds import CloudCreationError, create_cloud_chat

router = APIRouter(prefix="/api/v1/db/databases", tags=["json_databases"])
public_router = APIRouter(
    prefix="/api/v1/public/db/databases", tags=["json_databases_public"]
)

DATABASE_KEY_PREFIX = "lcdb_"
DATABASE_KEY_ENTROPY_LEN = 24
DATABASE_KEY_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
DATABASE_KEY_RE = re.compile(r"^lcdb_[a-z2-9]{24}$")
DatabaseKeyQuery = Annotated[
    str | None, Query(min_length=1, max_length=64, pattern=r"^lcdb_[a-z2-9]{24}$")
]


class CreateDatabaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("database_name_required")
        return name


def _new_database_key() -> str:
    body = "".join(
        secrets.choice(DATABASE_KEY_ALPHABET) for _ in range(DATABASE_KEY_ENTROPY_LEN)
    )
    return f"{DATABASE_KEY_PREFIX}{body}"


async def _allocate_database_key(sess: AsyncSession) -> str:
    key = _new_database_key()
    while (
        await sess.execute(
            sa.select(JsonDatabase.id).where(JsonDatabase.database_key == key)
        )
    ).scalar_one_or_none():
        key = _new_database_key()
    return key


def serialize_database(
    row: JsonDatabase,
    *,
    collection_count: int = 0,
    telegram_chat_id: int | None = None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "database_key": row.database_key,
        "name": row.name,
        "owner_user_id": row.owner_user_id,
        "cloud_id": row.cloud_id,
        "telegram_chat_id": telegram_chat_id,
        "telegram_backed": row.cloud_id is not None,
        "is_default": row.is_default,
        "collection_count": collection_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def get_database_or_404(
    sess: AsyncSession,
    *,
    user: User,
    database_id: int,
) -> JsonDatabase:
    row = (
        await sess.execute(
            sa.select(JsonDatabase).where(JsonDatabase.id == database_id)
        )
    ).scalar_one_or_none()
    if row is None or (user.role != "admin" and row.owner_user_id != user.id):
        raise HTTPException(404, detail={"reason": "database_not_found"})
    return row


async def get_database_by_key_or_404(
    sess: AsyncSession,
    *,
    user: User,
    database_key: str,
) -> JsonDatabase:
    row = (
        await sess.execute(
            sa.select(JsonDatabase).where(JsonDatabase.database_key == database_key)
        )
    ).scalar_one_or_none()
    if row is None or (user.role != "admin" and row.owner_user_id != user.id):
        raise HTTPException(404, detail={"reason": "database_not_found"})
    return row


async def get_default_database(
    sess: AsyncSession,
    *,
    owner_user_id: int,
    create: bool = True,
) -> JsonDatabase:
    row = (
        await sess.execute(
            sa.select(JsonDatabase)
            .where(JsonDatabase.owner_user_id == owner_user_id)
            .order_by(JsonDatabase.is_default.desc(), JsonDatabase.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    if not create:
        raise HTTPException(404, detail={"reason": "database_not_found"})
    row = JsonDatabase(
        owner_user_id=owner_user_id,
        database_key=await _allocate_database_key(sess),
        name="Default database",
        is_default=True,
        updated_at=datetime.now(UTC),
    )
    sess.add(row)
    await sess.flush()
    return row


async def resolve_database(
    sess: AsyncSession,
    *,
    user: User,
    database_id: int | None,
    database_key: str | None = None,
) -> JsonDatabase:
    if database_id is not None and database_key is not None:
        row = await get_database_or_404(sess, user=user, database_id=database_id)
        if row.database_key != database_key:
            raise HTTPException(400, detail={"reason": "database_scope_conflict"})
        return row
    if database_key is not None:
        return await get_database_by_key_or_404(
            sess, user=user, database_key=database_key
        )
    if database_id is not None:
        return await get_database_or_404(sess, user=user, database_id=database_id)
    return await get_default_database(sess, owner_user_id=user.id)


async def _ensure_userbot_ready(manager: UserbotManager) -> None:
    if not manager.is_started:
        raise HTTPException(503, detail={"reason": "userbot_not_started"})
    if not (await manager.snapshot()).authorized:
        raise HTTPException(409, detail={"reason": "userbot_not_authorized"})


async def _admin_owner_id(sess: AsyncSession) -> int:
    owner = (
        await sess.execute(sa.select(Owner).where(Owner.role == "admin").limit(1))
    ).scalar_one_or_none()
    if owner is None:
        raise HTTPException(503, detail={"reason": "admin_owner_missing"})
    return owner.id


@router.get("")
async def list_databases(user: CurrentUser) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        query = (
            sa.select(JsonDatabase, sa.func.count(JsonCollection.id), Cloud.chat_id)
            .outerjoin(JsonCollection, JsonCollection.database_id == JsonDatabase.id)
            .outerjoin(Cloud, Cloud.id == JsonDatabase.cloud_id)
            .group_by(JsonDatabase.id, Cloud.chat_id)
            .order_by(JsonDatabase.updated_at.desc(), JsonDatabase.id.desc())
        )
        if user.role != "admin":
            query = query.where(JsonDatabase.owner_user_id == user.id)
        rows = (await sess.execute(query)).all()
    return [
        serialize_database(
            row,
            collection_count=int(count),
            telegram_chat_id=int(chat_id) if chat_id is not None else None,
        )
        for row, count, chat_id in rows
    ]


@router.post("", status_code=201)
async def create_database(
    body: CreateDatabaseIn,
    user: CurrentUser,
    manager: UserbotManager = Depends(get_userbot_manager),
) -> dict[str, Any]:
    await _ensure_userbot_ready(manager)
    name = body.name.strip()
    sm = get_sessionmaker()
    async with sm() as sess:
        duplicate = (
            await sess.execute(
                sa.select(JsonDatabase.id).where(
                    JsonDatabase.owner_user_id == user.id,
                    JsonDatabase.name == name,
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(409, detail={"reason": "database_exists"})
        admin_owner_id = await _admin_owner_id(sess)

    sk, _ = ensure_admin_keypair()
    try:
        chat_id, marker, _channel = await create_cloud_chat(
            manager.client,
            name=f"LCloud DB - {name}",
            signing_key=sk,
        )
    except CloudCreationError as exc:
        raise HTTPException(
            502, detail={"reason": "database_chat_creation_failed", "error": str(exc)}
        ) from exc
    except RPCError as exc:
        raise HTTPException(
            502, detail={"reason": "telegram_rpc_error", "error": str(exc)}
        ) from exc

    async with sm() as sess:
        cloud = Cloud(
            chat_id=chat_id,
            owner_id=admin_owner_id,
            owner_user_id=user.id,
            name=f"DB: {name}",
            about=marker,
        )
        sess.add(cloud)
        await sess.flush()
        database = JsonDatabase(
            owner_user_id=user.id,
            cloud_id=cloud.id,
            database_key=await _allocate_database_key(sess),
            name=name,
            is_default=False,
            updated_at=datetime.now(UTC),
        )
        sess.add(database)
        await sess.commit()
        await sess.refresh(database)
    await invalidate_user_clouds(user.id)
    await invalidate_json_database(database.id)
    return serialize_database(database, telegram_chat_id=chat_id)


@public_router.get("/{database_key}")
async def resolve_public_database(database_key: str) -> dict[str, Any]:
    key = database_key.strip()
    if not DATABASE_KEY_RE.match(key):
        raise HTTPException(404, detail={"reason": "database_not_found"})
    cache_key = k_json_database_key(key)
    cached = await cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(JsonDatabase).where(JsonDatabase.database_key == key)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "database_not_found"})
        body = {
            "id": row.id,
            "database_key": row.database_key,
            "name": row.name,
            "telegram_backed": row.cloud_id is not None,
        }
    await cache.set(cache_key, body, ttl=JSON_DB_TTL, namespace="json_database")
    return body
