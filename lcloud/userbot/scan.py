"""Dialog scanner: walk Telegram dialogs and reconcile cloud markers with DB.

Run after every successful userbot authorization (lifespan boot or fresh
web-login). Idempotent: re-running updates names/about for known clouds
and inserts new ones; chats whose marker was removed are NOT auto-deleted
from the DB (operator decision, per goal.md §9 `DELETE /clouds/{id}`).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel
from telethon.utils import get_peer_id

from lcloud.db.models import Cloud
from lcloud.userbot.marker import parse_marker, verify_marker
from lcloud.workers import mtproto_call

logger = logging.getLogger(__name__)


async def scan_dialogs_for_clouds(
    client: TelegramClient,
    *,
    sessionmaker: async_sessionmaker[Any],
    owner_id: int,
    expected_pubkey: bytes,
) -> int:
    """Iterate dialogs, parse + verify LCLOUD1 markers, upsert into `clouds`.

    Returns the number of cloud rows present in the DB after the scan
    completes (i.e. how many clouds we currently track for this admin).
    """
    seen = 0
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        # Only supergroups (megagroup=True) carry a public `about` we set.
        if not isinstance(entity, Channel) or not getattr(entity, "megagroup", False):
            continue
        try:
            full = await mtproto_call(client, GetFullChannelRequest(channel=entity))
        except Exception:  # pragma: no cover — Telethon flood / permission
            logger.exception("GetFullChannelRequest failed for chat %s", entity.id)
            continue
        about: str = getattr(full.full_chat, "about", "") or ""
        parsed = parse_marker(about)
        if parsed is None:
            continue
        marked_id: int = get_peer_id(entity)
        if not verify_marker(
            parsed, chat_id=marked_id, expected_pubkey=expected_pubkey
        ):
            logger.warning(
                "marker present on chat %s but signature invalid; ignoring",
                marked_id,
            )
            continue

        async with sessionmaker() as sess:
            existing = await sess.execute(
                sa.select(Cloud).where(Cloud.chat_id == marked_id)
            )
            row = existing.scalar_one_or_none()
            title = getattr(entity, "title", "") or ""
            if row is None:
                sess.add(
                    Cloud(
                        chat_id=marked_id,
                        owner_id=owner_id,
                        name=title,
                        about=about,
                    )
                )
            else:
                row.name = title
                row.about = about
            await sess.commit()
        seen += 1
    return seen


def schedule_scan(
    *,
    client: TelegramClient,
    sessionmaker: async_sessionmaker[Any],
    owner_id: int,
    expected_pubkey: bytes,
) -> asyncio.Task[int]:
    """Fire-and-forget background scan. Returns the Task so callers can await
    it during tests; production code lets it run unawaited.
    """

    async def _run() -> int:
        try:
            n = await scan_dialogs_for_clouds(
                client,
                sessionmaker=sessionmaker,
                owner_id=owner_id,
                expected_pubkey=expected_pubkey,
            )
            logger.info("dialog scan complete; %d cloud(s) tracked", n)
            return n
        except Exception:
            logger.exception("dialog scan failed")
            return 0

    return asyncio.create_task(_run())
