"""Cloud-chat operations: create / scan / disconnect via Telethon.

All MTProto calls go through `lcloud.workers.mtproto_call` so they share
the global token-bucket rate limiter and FloodWait retry policy.
"""

from __future__ import annotations

import logging
from typing import Any

from nacl.signing import SigningKey
from telethon import TelegramClient
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.functions.messages import EditChatAboutRequest
from telethon.tl.types import Channel
from telethon.utils import get_peer_id

from lcloud.userbot.marker import build_marker, parse_marker, verify_marker
from lcloud.workers import mtproto_call

logger = logging.getLogger(__name__)


class CloudCreationError(Exception):
    pass


async def create_cloud_chat(
    client: TelegramClient,
    *,
    name: str,
    signing_key: SigningKey,
) -> tuple[int, str, Channel]:
    """Create a Telegram supergroup, set the LCLOUD1 marker as its `about`.

    Returns ``(marked_chat_id, marker, channel_entity)``.
    """
    result: Any = await mtproto_call(
        client, CreateChannelRequest(title=name, about="", megagroup=True)
    )
    channel: Channel | None = None
    for chat in getattr(result, "chats", []) or []:
        if isinstance(chat, Channel):
            channel = chat
            break
    if channel is None:
        raise CloudCreationError("CreateChannelRequest did not return a Channel")

    marked_id: int = get_peer_id(channel)
    marker = build_marker(signing_key=signing_key, chat_id=marked_id)
    await mtproto_call(client, EditChatAboutRequest(peer=channel, about=marker))
    logger.info("created cloud supergroup id=%s name=%r", marked_id, name)
    return marked_id, marker, channel


async def connect_existing_cloud_chat(
    client: TelegramClient,
    *,
    chat: Channel,
    signing_key: SigningKey,
) -> tuple[int, str]:
    """Stamp the LCLOUD1 marker on an EXISTING supergroup. Caller resolved
    the entity already (via get_entity) and verified it's a megagroup.

    Returns ``(marked_chat_id, marker)``.
    """
    if not isinstance(chat, Channel) or not getattr(chat, "megagroup", False):
        raise CloudCreationError("only supergroups can become clouds")
    marked_id: int = get_peer_id(chat)
    marker = build_marker(signing_key=signing_key, chat_id=marked_id)
    await mtproto_call(client, EditChatAboutRequest(peer=chat, about=marker))
    logger.info("marked existing chat as cloud id=%s", marked_id)
    return marked_id, marker


async def clear_cloud_marker(
    client: TelegramClient, *, channel: Channel
) -> None:
    """Best-effort: clear `chat.about` for an existing cloud chat (disconnect)."""
    await mtproto_call(client, EditChatAboutRequest(peer=channel, about=""))


__all__ = [
    "CloudCreationError",
    "build_marker",
    "clear_cloud_marker",
    "connect_existing_cloud_chat",
    "create_cloud_chat",
    "parse_marker",
    "verify_marker",
]
