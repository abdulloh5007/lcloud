"""V2 per-user clouds endpoints (under /api/v1/clouds).

Each user owns clouds (TG supergroups). The TG account hosting these is
still the single bootstrap admin's — the userbot creates the supergroup,
embeds an LCLOUD1 marker signed by the admin's V1 key, and we record the
calling V2 user's id in `clouds.owner_user_id`.

Filtering for list/delete is by `owner_user_id`; admins additionally see
all clouds (cross-user).
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from telethon.errors import RPCError

from lcloud.auth.v2_deps import CurrentUser
from lcloud.crypto.keys import ensure_admin_keypair
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import Cloud, Owner
from lcloud.userbot.client import UserbotManager, get_userbot_manager
from lcloud.userbot.clouds import (
    CloudCreationError,
    clear_cloud_marker,
    create_cloud_chat,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/clouds", tags=["v2_clouds"])


class CreateCloudIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)


def _serialize(c: Cloud) -> dict[str, Any]:
    return {
        "id": c.id,
        "chat_id": c.chat_id,
        "name": c.name,
        "owner_user_id": c.owner_user_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


async def _ensure_userbot_ready(manager: UserbotManager) -> None:
    if not manager.is_started:
        raise HTTPException(503, detail={"reason": "userbot_not_started"})
    snap = await manager.snapshot()
    if not snap.authorized:
        raise HTTPException(409, detail={"reason": "userbot_not_authorized"})


async def _admin_owner_id() -> int:
    """Get the bootstrap admin's owner_id (single row, used for TG signing)."""
    sm = get_sessionmaker()
    async with sm() as sess:
        owner = (
            await sess.execute(
                sa.select(Owner).where(Owner.role == "admin").limit(1)
            )
        ).scalar_one_or_none()
    if owner is None:
        raise HTTPException(503, detail={"reason": "admin_owner_missing"})
    return owner.id


@router.get(
    "",
    summary="Список ваших облаков",
    description=(
        "Возвращает все cloud-ы (TG-супергруппы), которые принадлежат вам. "
        "Admin видит все cloud-ы всех пользователей. Сортировка: новые сверху."
    ),
)
async def list_clouds(user: CurrentUser) -> list[dict[str, Any]]:
    sm = get_sessionmaker()
    async with sm() as sess:
        # Admins see all clouds across all users; regular users see only their own
        stmt = sa.select(Cloud).order_by(Cloud.created_at.desc())
        if user.role != "admin":
            stmt = stmt.where(Cloud.owner_user_id == user.id)
        rows = (await sess.execute(stmt)).scalars().all()
    return [_serialize(c) for c in rows]


@router.post(
    "",
    status_code=201,
    summary="Создать новый cloud",
    description=(
        "Создаёт новую TG-супергруппу под управлением админ-аккаунта (юзербот). "
        "DB-запись `clouds` помечается owner_user_id=ваш_id. "
        "Только владелец видит свой cloud в списке (admin видит все)."
    ),
)
async def create_cloud(
    body: CreateCloudIn,
    user: CurrentUser,
    manager: UserbotManager = Depends(get_userbot_manager),
) -> dict[str, Any]:
    await _ensure_userbot_ready(manager)
    admin_owner_id = await _admin_owner_id()
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
            chat_id=chat_id,
            owner_id=admin_owner_id,  # TG-side signer is still admin
            owner_user_id=user.id,  # logical owner is the V2 user
            name=body.name,
            about=marker,
        )
        sess.add(cloud)
        await sess.commit()
        await sess.refresh(cloud)
    return _serialize(cloud)


@router.delete(
    "/{cloud_id}",
    status_code=204,
    response_class=Response,
    summary="Отключить cloud",
    description=(
        "Очищает LCLOUD1-маркер в `chat.about` и удаляет DB-запись. "
        "Сама TG-супергруппа НЕ удаляется (per spec §9). После этого "
        "файлы в этом cloud-е больше не индексируются LCloud-ом, но "
        "физически остаются в Telegram. Можно подключить обратно через "
        "/lc_connect или удалить вручную в Telegram."
    ),
)
async def disconnect_cloud(
    cloud_id: int,
    user: CurrentUser,
    manager: UserbotManager = Depends(get_userbot_manager),
) -> Response:
    await _ensure_userbot_ready(manager)
    sm = get_sessionmaker()
    async with sm() as sess:
        cloud = (
            await sess.execute(sa.select(Cloud).where(Cloud.id == cloud_id))
        ).scalar_one_or_none()
        if cloud is None:
            raise HTTPException(404, detail={"reason": "not_found"})
        # Authorization: admins delete anything, users only their own
        if user.role != "admin" and cloud.owner_user_id != user.id:
            raise HTTPException(403, detail={"reason": "forbidden"})

    try:
        entity = await manager.client.get_entity(cloud.chat_id)
        await clear_cloud_marker(manager.client, channel=entity)
    except Exception:
        logger.warning(
            "could not clear marker for cloud %s; deleting DB row anyway",
            cloud_id,
            exc_info=True,
        )

    async with sm() as sess:
        await sess.execute(sa.delete(Cloud).where(Cloud.id == cloud_id))
        await sess.commit()
    return Response(status_code=204)


__all__ = ["router"]
