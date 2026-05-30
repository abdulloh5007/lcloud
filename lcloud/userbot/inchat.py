"""In-chat slash commands: `/lc_connect` and `/lc_disconnect`.

When the admin (the userbot's own account) types `/lc_connect` in any
supergroup, that chat becomes a tracked cloud — marker stamped, DB row
created. `/lc_disconnect` reverses it for the current chat.

Filter: `outgoing=True, incoming=False` + regex `^/lc_(connect|disconnect)\\b`.
Private chats (`event.is_private`) and Saved Messages are excluded — those
go through the dedicated Saved-Messages handler instead.

Confirmation messages are delivered to Saved Messages so they don't pollute
the actual chat. The original command message is deleted on success.
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from nacl.signing import SigningKey
from sqlalchemy.ext.asyncio import async_sessionmaker
from telethon import TelegramClient, events
from telethon.tl.types import Channel
from telethon.utils import get_peer_id

from lcloud.db.models import Cloud
from lcloud.userbot.clouds import clear_cloud_marker, connect_existing_cloud_chat

logger = logging.getLogger(__name__)

_CMD_RE = re.compile(r"^/lc_(connect|disconnect)\b\s*(.*)$")


@dataclass
class InChatContext:
    sessionmaker: async_sessionmaker[Any]
    owner_id: int
    signing_key: SigningKey


async def _alert(client: Any, text: str) -> None:
    with contextlib.suppress(Exception):
        await client.send_message("me", text, link_preview=False)


async def _delete_msg_silent(message: Any) -> None:
    with contextlib.suppress(Exception):
        await message.delete()


async def handle_in_chat_command(event: Any, ctx: InChatContext) -> str | None:
    """Process one outgoing /lc_* event. Returns short status string for tests
    or None if not a recognised command."""
    msg = getattr(event, "message", None)
    if msg is None:
        return None
    if not getattr(msg, "out", False):
        return None
    text = (getattr(msg, "message", None) or "").strip()
    m = _CMD_RE.match(text)
    if not m:
        return None
    cmd, arg = m.group(1), m.group(2).strip()

    client = getattr(event, "client", None)
    if client is None:
        return None

    # Private chats / Saved Messages → wrong handler
    if getattr(event, "is_private", False):
        await _alert(client, "❕ /lc_* commands run inside a supergroup, not Saved Messages.")
        await _delete_msg_silent(msg)
        return "skip_private"

    chat = await event.get_chat()
    if not isinstance(chat, Channel) or not getattr(chat, "megagroup", False):
        await _alert(client, "⚠️ /lc_* commands only work in supergroups.")
        await _delete_msg_silent(msg)
        return "skip_not_supergroup"

    marked_id = get_peer_id(chat)
    title = getattr(chat, "title", None) or "untitled"

    if cmd == "connect":
        # Already connected?
        async with ctx.sessionmaker() as sess:
            existing = (
                await sess.execute(
                    sa.select(Cloud).where(Cloud.chat_id == marked_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                await _alert(
                    client,
                    f"❕ Already connected: *{existing.name}* (#{existing.id})",
                )
                await _delete_msg_silent(msg)
                return "already_connected"

        try:
            _, marker = await connect_existing_cloud_chat(
                client, chat=chat, signing_key=ctx.signing_key
            )
        except Exception as exc:
            logger.exception("/lc_connect failed for chat %s", marked_id)
            await _alert(
                client, f"❌ couldn't set marker (need admin in chat?): {exc}"
            )
            await _delete_msg_silent(msg)
            return "marker_failed"

        # Optional name override from arg
        chosen_name = arg if arg else title

        async with ctx.sessionmaker() as sess:
            row = Cloud(
                chat_id=marked_id,
                owner_id=ctx.owner_id,
                name=chosen_name,
                about=marker,
            )
            sess.add(row)
            await sess.commit()
            await sess.refresh(row)
        await _alert(
            client, f"✅ Connected: *{chosen_name}* (#{row.id}, {marked_id})"
        )
        await _delete_msg_silent(msg)
        return "connected"

    if cmd == "disconnect":
        async with ctx.sessionmaker() as sess:
            row = (
                await sess.execute(
                    sa.select(Cloud).where(
                        Cloud.chat_id == marked_id,
                        Cloud.owner_id == ctx.owner_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await _alert(client, f"❕ This chat is not a cloud ({marked_id}).")
                await _delete_msg_silent(msg)
                return "not_a_cloud"
            cloud_id = row.id
            cloud_name = row.name

        with contextlib.suppress(Exception):
            await clear_cloud_marker(client, channel=chat)

        async with ctx.sessionmaker() as sess:
            row = (
                await sess.execute(sa.select(Cloud).where(Cloud.id == cloud_id))
            ).scalar_one_or_none()
            if row is not None:
                await sess.delete(row)
                await sess.commit()

        await _alert(
            client, f"✅ Disconnected: *{cloud_name}* (#{cloud_id}, {marked_id})"
        )
        await _delete_msg_silent(msg)
        return "disconnected"

    return None


def register_in_chat_handlers(
    client: TelegramClient, ctx: InChatContext
) -> None:
    async def _on(event: events.NewMessage.Event) -> None:
        try:
            await handle_in_chat_command(event, ctx)
        except Exception:
            logger.exception("/lc_* handler crashed (chat=%s)", event.chat_id)

    client.add_event_handler(
        _on,
        events.NewMessage(
            outgoing=True, incoming=False, pattern=r"^/lc_(connect|disconnect)\b"
        ),
    )
    logger.info("in-chat /lc_* command handler attached")
