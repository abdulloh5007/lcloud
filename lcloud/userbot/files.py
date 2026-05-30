"""File operations on cloud chats: upload / download / delete via Telethon.

Upload flow per goal.md §5:
    1. Upload the binary to the chat (no caption yet — we don't know
       message_id until after).
    2. Compute the canonical signature payload, sign it.
    3. Edit the message caption to LC1:{...} so the metadata is durable
       on Telegram's side too (DB is the fast index, caption is the
       authoritative copy).

Download streams chunks from Telegram → caller.
Delete removes the Telegram message AND soft-deletes the DB row (caller's
responsibility for the DB part; this module only touches Telegram).
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from nacl.signing import SigningKey
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename

from lcloud.crypto.sign import file_signature_payload, sign
from lcloud.userbot.lc1 import build_lc1_caption

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadResult:
    message_id: int
    caption: str
    uploaded_at_unix: int
    signature: bytes


async def upload_file_to_cloud(
    client: TelegramClient,
    *,
    chat_id: int,
    file_path: Path,
    original_name: str,
    sha256_digest: bytes,
    signing_key: SigningKey,
) -> UploadResult:
    """Send `file_path` to the cloud chat as a document; stamp LC1 caption.

    Note: high-level Telethon `send_file` / `edit_message` are not wrapped
    in `mtproto_call` — they are higher-level helpers that internally manage
    chunking + per-chunk FloodWait already. The token bucket gates our
    raw TL requests (CreateChannel/EditAbout/GetFullChannel/etc.).
    """
    entity = await client.get_entity(chat_id)
    msg = await client.send_file(
        entity,
        file=str(file_path),
        force_document=True,
        attributes=[DocumentAttributeFilename(file_name=original_name)],
    )
    message_id: int = int(msg.id)
    uploaded_at_unix = int(time.time())

    payload = file_signature_payload(
        sha256_digest=sha256_digest,
        chat_id=chat_id,
        message_id=message_id,
        owner_pubkey=bytes(signing_key.verify_key),
        uploaded_at_unix=uploaded_at_unix,
    )
    signature = sign(signing_key, payload)
    caption = build_lc1_caption(
        sha256_digest=sha256_digest,
        signature=signature,
        owner_pubkey=bytes(signing_key.verify_key),
        uploaded_at_unix=uploaded_at_unix,
    )
    await client.edit_message(entity, message_id, caption)
    logger.info(
        "uploaded file to chat=%s message_id=%s name=%r",
        chat_id,
        message_id,
        original_name,
    )
    return UploadResult(
        message_id=message_id,
        caption=caption,
        uploaded_at_unix=uploaded_at_unix,
        signature=signature,
    )


async def iter_download_file(
    client: TelegramClient,
    *,
    chat_id: int,
    message_id: int,
    chunk_size: int = 512 * 1024,
) -> AsyncIterator[bytes]:
    """Async generator yielding the file's bytes in `chunk_size` chunks."""
    entity = await client.get_entity(chat_id)
    message = await client.get_messages(entity, ids=message_id)
    if message is None or getattr(message, "media", None) is None:
        raise FileNotFoundError(f"no message {message_id} in chat {chat_id}")
    async for chunk in client.iter_download(message, chunk_size=chunk_size):
        yield chunk


async def delete_file_message(
    client: TelegramClient, *, chat_id: int, message_id: int
) -> None:
    entity = await client.get_entity(chat_id)
    await client.delete_messages(entity, [message_id])
    logger.info("deleted message_id=%s from chat=%s", message_id, chat_id)
