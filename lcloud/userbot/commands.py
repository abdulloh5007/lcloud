"""Saved-Messages slash commands.

Per goal.md §7.2 (with §A1 amendment dropping `/admin` magic-link):
    /help          show available commands
    /status        clouds + files + total size
    /revoke        invalidate all live web sessions (bumps auth_epoch)
    /createcloud   <name>          create new cloud (supergroup)
    /clouds                        list connected clouds
    /connect       <link|id|@u>    connect EXISTING chat as cloud
    /disconnect    <id>            unbind a cloud (clear marker + drop row)

Filter: messages must be `outgoing=True, incoming=False, chats="me"` —
i.e. the admin's own messages in their own Saved Messages, never
a third party's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from nacl.signing import SigningKey
from sqlalchemy.ext.asyncio import async_sessionmaker
from telethon import TelegramClient, events
from telethon.tl.types import Channel
from telethon.utils import get_peer_id

from lcloud.auth.jwt_utils import issue_magic_token
from lcloud.config import Settings
from lcloud.db.models import AuthState, Cloud, File, Owner
from lcloud.userbot.clouds import (
    CloudCreationError,
    clear_cloud_marker,
    connect_existing_cloud_chat,
    create_cloud_chat,
)

logger = logging.getLogger(__name__)


@dataclass
class CommandContext:
    sessionmaker: async_sessionmaker[Any]
    owner_id: int
    signing_key: SigningKey
    settings: Settings


HELP_TEXT = (
    "📂 *LCloud commands*\n"
    "/help — show this message\n"
    "/admin — get a one-time admin web-login link\n"
    "/status — clouds + files + total size\n"
    "/clouds — list connected clouds\n"
    "/createcloud <name> — create a new cloud supergroup\n"
    "/connect <link|@username|id> — connect existing chat\n"
    "/disconnect <cloud_id> — unbind a cloud\n"
    "/revoke — invalidate all active web sessions\n"
    "\nIn-chat commands (run inside any supergroup):\n"
    "/lc_connect [name] — make this chat a cloud\n"
    "/lc_disconnect — unbind this chat"
)


# ------------------------------------------------------------------ basic commands


async def _cmd_help(_event: Any, _ctx: CommandContext) -> str:
    return HELP_TEXT


async def _cmd_status(_event: Any, ctx: CommandContext) -> str:
    async with ctx.sessionmaker() as sess:
        clouds_n = (
            await sess.execute(
                sa.select(sa.func.count())
                .select_from(Cloud)
                .where(Cloud.owner_id == ctx.owner_id)
            )
        ).scalar_one()
        files_n = (
            await sess.execute(
                sa.select(sa.func.count())
                .select_from(File)
                .where(File.owner_id == ctx.owner_id, File.deleted_at.is_(None))
            )
        ).scalar_one()
        total = (
            await sess.execute(
                sa.select(sa.func.coalesce(sa.func.sum(File.size_bytes), 0))
                .where(File.owner_id == ctx.owner_id, File.deleted_at.is_(None))
            )
        ).scalar_one()
    gib = float(total) / (1024 * 1024 * 1024)
    return (
        f"📊 *LCloud status*\n"
        f"Clouds: {clouds_n}\n"
        f"Files: {files_n}\n"
        f"Total size: {gib:.2f} GiB ({int(total):,} bytes)"
    )


async def _cmd_revoke(_event: Any, ctx: CommandContext) -> str:
    async with ctx.sessionmaker() as sess:
        result = await sess.execute(
            sa.select(AuthState).where(AuthState.owner_id == ctx.owner_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = AuthState(owner_id=ctx.owner_id, epoch=2)
            sess.add(row)
        else:
            row.epoch = int(row.epoch) + 1
        await sess.commit()
        new_epoch = row.epoch
    logger.info("auth_epoch bumped to %s for owner %s", new_epoch, ctx.owner_id)
    return f"✅ All web sessions invalidated (auth_epoch={new_epoch})."


async def _cmd_admin(_event: Any, ctx: CommandContext) -> str:
    """Generate a one-time, short-TTL admin web login link."""
    async with ctx.sessionmaker() as sess:
        owner = (
            await sess.execute(
                sa.select(Owner).where(
                    Owner.id == ctx.owner_id, Owner.role == "admin"
                )
            )
        ).scalar_one_or_none()
        if owner is None:
            return "❌ admin owner row missing"
        epoch = (
            await sess.execute(
                sa.select(AuthState.epoch).where(
                    AuthState.owner_id == ctx.owner_id
                )
            )
        ).scalar_one_or_none()
        current_epoch = int(epoch) if epoch is not None else 1

    token = issue_magic_token(
        owner_id=ctx.owner_id,
        auth_epoch=current_epoch,
        settings=ctx.settings,
    )
    base = ctx.settings.lc_public_base_url.rstrip("/")
    url = f"{base}/admin?token={token}"
    ttl_min = ctx.settings.lc_magic_link_ttl_seconds // 60
    return (
        f"🔗 *LCloud admin link* (one-time, valid {ttl_min} min):\n{url}"
    )


# ------------------------------------------------------------------ cloud commands


async def _cmd_clouds(_event: Any, ctx: CommandContext) -> str:
    async with ctx.sessionmaker() as sess:
        rows = (
            await sess.execute(
                sa.select(Cloud)
                .where(Cloud.owner_id == ctx.owner_id)
                .order_by(Cloud.id)
            )
        ).scalars().all()
    if not rows:
        return "❕ No clouds yet. Use /createcloud <name> or /connect <link>."
    lines = ["📁 *Connected clouds*"]
    for r in rows:
        lines.append(f"  #{r.id} · {r.name}  ({r.chat_id})")
    return "\n".join(lines)


async def _cmd_createcloud(event: Any, ctx: CommandContext) -> str:
    text = (event.message.message or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return "usage: /createcloud <name>"
    name = parts[1].strip()
    if len(name) > 128:
        return "❌ name too long (max 128 chars)"
    try:
        marked_id, _marker, channel = await create_cloud_chat(
            event.client, name=name, signing_key=ctx.signing_key
        )
    except CloudCreationError as exc:
        return f"❌ couldn't create chat: {exc}"
    except Exception as exc:
        logger.exception("createcloud failed")
        return f"❌ telegram error: {exc}"

    async with ctx.sessionmaker() as sess:
        row = Cloud(
            chat_id=marked_id,
            owner_id=ctx.owner_id,
            name=name,
            about=_marker,
        )
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)
    username = getattr(channel, "username", None)
    link = f"https://t.me/{username}" if username else f"chat_id={marked_id}"
    return (
        f"✅ Created cloud *{name}* (#{row.id})\n"
        f"{link}\n"
        f"Now drop files into this chat — they'll appear in the web UI."
    )


async def _resolve_chat(event: Any, target: str) -> Channel | None:
    """Resolve a chat link / username / id to a Channel entity. Returns
    None if not resolvable or not a supergroup."""
    try:
        # Numeric ids — Telethon expects int
        normalized: int | str = target
        if target.lstrip("-").isdigit():
            normalized = int(target)
        entity = await event.client.get_entity(normalized)
    except Exception:
        return None
    if not isinstance(entity, Channel) or not getattr(entity, "megagroup", False):
        return None
    return entity


async def _cmd_connect(event: Any, ctx: CommandContext) -> str:
    text = (event.message.message or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return "usage: /connect <link|@username|chat_id>"
    target = parts[1].strip()
    entity = await _resolve_chat(event, target)
    if entity is None:
        return f"❌ couldn't resolve {target!r} as a supergroup"
    marked_id = get_peer_id(entity)

    async with ctx.sessionmaker() as sess:
        existing = (
            await sess.execute(sa.select(Cloud).where(Cloud.chat_id == marked_id))
        ).scalar_one_or_none()
        if existing is not None:
            return f"❕ Already connected as cloud #{existing.id}: {existing.name}"

    try:
        _, marker = await connect_existing_cloud_chat(
            event.client, chat=entity, signing_key=ctx.signing_key
        )
    except Exception as exc:
        logger.exception("connect failed")
        return f"❌ couldn't set marker (need admin in chat?): {exc}"

    title = getattr(entity, "title", None) or "untitled"
    async with ctx.sessionmaker() as sess:
        row = Cloud(
            chat_id=marked_id,
            owner_id=ctx.owner_id,
            name=title,
            about=marker,
        )
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)
    return f"✅ Connected: *{title}* (#{row.id}, {marked_id})"


async def _cmd_disconnect(event: Any, ctx: CommandContext) -> str:
    text = (event.message.message or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        return "usage: /disconnect <cloud_id>"
    cloud_id = int(parts[1].strip())

    async with ctx.sessionmaker() as sess:
        row = (
            await sess.execute(
                sa.select(Cloud).where(
                    Cloud.id == cloud_id, Cloud.owner_id == ctx.owner_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return f"❌ cloud #{cloud_id} not found"
        chat_id = row.chat_id
        name = row.name

    # Best-effort marker clear
    try:
        entity = await event.client.get_entity(chat_id)
        await clear_cloud_marker(event.client, channel=entity)
    except Exception:
        logger.warning("could not clear marker for cloud %s", cloud_id, exc_info=True)

    async with ctx.sessionmaker() as sess:
        row = (
            await sess.execute(sa.select(Cloud).where(Cloud.id == cloud_id))
        ).scalar_one_or_none()
        if row is not None:
            await sess.delete(row)
            await sess.commit()
    return f"✅ Disconnected cloud *{name}* (#{cloud_id})"


_COMMANDS = {
    "/help": _cmd_help,
    "/admin": _cmd_admin,
    "/status": _cmd_status,
    "/revoke": _cmd_revoke,
    "/clouds": _cmd_clouds,
    "/createcloud": _cmd_createcloud,
    "/connect": _cmd_connect,
    "/disconnect": _cmd_disconnect,
}


async def handle_saved_messages_command(
    event: Any, ctx: CommandContext
) -> str | None:
    msg = getattr(event, "message", None)
    if msg is None:
        return None
    if not getattr(msg, "out", False):
        return None
    text = (getattr(msg, "message", None) or "").strip()
    if not text.startswith("/"):
        return None
    cmd = text.split()[0].lower()
    handler = _COMMANDS.get(cmd)
    if handler is None:
        return None
    reply = await handler(event, ctx)
    client = getattr(event, "client", None)
    if client is not None:
        try:
            await client.send_message("me", reply, link_preview=False)
        except Exception:  # pragma: no cover — telethon flood
            logger.exception("could not send Saved-Messages reply")
    return reply


def register_saved_messages_handlers(
    client: TelegramClient, ctx: CommandContext
) -> None:
    async def _on_saved(event: events.NewMessage.Event) -> None:
        try:
            await handle_saved_messages_command(event, ctx)
        except Exception:
            logger.exception("Saved-Messages command handler crashed")

    client.add_event_handler(
        _on_saved,
        events.NewMessage(chats="me", outgoing=True, incoming=False),
    )
    logger.info("Saved-Messages command handler attached")
