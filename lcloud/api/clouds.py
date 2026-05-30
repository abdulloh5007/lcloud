"""Clouds router: list / create / disconnect cloud supergroups."""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from telethon.errors import RPCError

from lcloud.auth.deps import require_admin
from lcloud.crypto.keys import ensure_admin_keypair
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import Cloud
from lcloud.userbot.client import UserbotManager, get_userbot_manager
from lcloud.userbot.clouds import (
    CloudCreationError,
    clear_cloud_marker,
    create_cloud_chat,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/clouds", tags=["clouds"])


class CreateCloudIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)


def _serialize_cloud(c: Cloud) -> dict[str, Any]:
    return {
        "id": c.id,
        "chat_id": c.chat_id,
        "name": c.name,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


async def _ensure_admin_userbot_ready(manager: UserbotManager) -> None:
    if not manager.is_started:
        raise HTTPException(503, detail={"reason": "userbot_not_started"})
    snap = await manager.snapshot()
    if not snap.authorized:
        raise HTTPException(409, detail={"reason": "userbot_not_authorized"})


@router.get("")
async def list_clouds(
    owner_id: int = Depends(require_admin),
) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(Cloud)
            .where(Cloud.owner_id == owner_id)
            .order_by(Cloud.created_at.desc())
        )
        clouds = result.scalars().all()
    return [_serialize_cloud(c) for c in clouds]


@router.post("", status_code=201)
async def create_cloud(
    body: CreateCloudIn,
    owner_id: int = Depends(require_admin),
    manager: UserbotManager = Depends(get_userbot_manager),
) -> dict[str, Any]:
    await _ensure_admin_userbot_ready(manager)
    sk, _ = ensure_admin_keypair()
    try:
        chat_id, marker, _channel = await create_cloud_chat(
            manager.client, name=body.name, signing_key=sk
        )
    except CloudCreationError as exc:
        raise HTTPException(
            502, detail={"reason": "cloud_creation_failed", "error": str(exc)}
        ) from exc
    except RPCError as exc:
        raise HTTPException(
            502, detail={"reason": "telegram_rpc_error", "error": str(exc)}
        ) from exc

    sm = get_sessionmaker()
    async with sm() as sess:
        cloud = Cloud(
            chat_id=chat_id, owner_id=owner_id, name=body.name, about=marker
        )
        sess.add(cloud)
        await sess.commit()
        await sess.refresh(cloud)
    return _serialize_cloud(cloud)


@router.delete("/{cloud_id}", status_code=204, response_class=Response)
async def disconnect_cloud(
    cloud_id: int,
    owner_id: int = Depends(require_admin),
    manager: UserbotManager = Depends(get_userbot_manager),
) -> Response:
    """Disconnect: clear the LCLOUD1 marker in `chat.about` and drop the row.

    Per goal.md §9 the chat itself stays in Telegram (we don't delete it).
    Best-effort: if marker clear fails (e.g., we lost admin in the chat), the
    DB row is still removed so the UI no longer shows the cloud.
    """
    await _ensure_admin_userbot_ready(manager)
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
        chat_id = cloud.chat_id

    try:
        entity = await manager.client.get_entity(chat_id)
        await clear_cloud_marker(manager.client, channel=entity)
    except Exception:
        logger.warning(
            "could not clear marker for cloud %s; deleting DB row anyway",
            cloud_id,
            exc_info=True,
        )

    async with sm() as sess:
        result = await sess.execute(
            sa.select(Cloud).where(Cloud.id == cloud_id)
        )
        cloud = result.scalar_one_or_none()
        if cloud is not None:
            await sess.delete(cloud)
            await sess.commit()
    return Response(status_code=204)
