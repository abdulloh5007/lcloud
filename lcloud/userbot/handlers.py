"""Telethon NewMessage handler: ingest files dropped into cloud chats.

Per goal.md §7.3:
- Filter to messages whose `chat_id` is a tracked cloud (DB lookup).
- Skip messages whose caption already starts with `LC1:` (our own writes).
- Photos are deliberately skipped in V1 — Telegram compresses them and the
  contract for the cloud is "exact bytes preserved", which only Documents
  give us. Send the photo as a Document to ingest it.
- For Documents: read size/mime/name from `Document` metadata, enforce
  the 1 GiB cap upfront (delete + Saved-Messages alert if exceeded),
  otherwise stream-download to compute sha256, sign, edit caption to
  LC1, persist a `files` row.

The handler is `outgoing=True, incoming=True` because the userbot IS the
admin's account: a "drop from my mobile TG client" lands as outgoing on
the userbot's side.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from nacl.signing import SigningKey
from sqlalchemy.ext.asyncio import async_sessionmaker
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename

from lcloud.config import Settings
from lcloud.crypto.sign import file_signature_payload, sign
from lcloud.db.models import Cloud, File
from lcloud.userbot.lc1 import LC1_PREFIX, build_lc1_caption

logger = logging.getLogger(__name__)


@dataclass
class IngestContext:
    """Everything the new-message handler needs (passed in at registration)."""

    sessionmaker: async_sessionmaker[Any]
    signing_key: SigningKey
    settings: Settings
    owner_id: int


@dataclass(frozen=True)
class _DocMeta:
    size: int
    mime: str
    original_name: str


def _extract_document_meta(message: Any) -> _DocMeta | None:
    """Extract size / mime / original_name from a Telethon Message that holds
    a `Document` media. Returns None for non-Document media (photos etc.)."""
    doc = getattr(message, "document", None)
    if doc is None:
        return None
    size = int(getattr(doc, "size", 0) or 0)
    mime = str(getattr(doc, "mime_type", None) or "application/octet-stream")
    attrs = getattr(doc, "attributes", []) or []
    name = next(
        (
            getattr(a, "file_name", None)
            for a in attrs
            if isinstance(a, DocumentAttributeFilename)
        ),
        None,
    )
    if not name:
        name = f"file-{getattr(message, 'id', 0)}"
    return _DocMeta(size=size, mime=mime, original_name=name)


async def _alert_admin(client: Any, text: str) -> None:
    """Send a one-line alert to the admin's Saved Messages."""
    try:
        await client.send_message("me", text)
    except Exception:  # pragma: no cover — telethon flood / network
        logger.exception("could not deliver Saved-Messages alert")


async def _stream_sha256(client: Any, message: Any) -> tuple[int, bytes]:
    """Download the message's media chunk-by-chunk through sha256.

    We DON'T persist the bytes to disk — only the hash is needed for the
    signature. The Telegram message itself is the canonical storage.
    """
    h = hashlib.sha256()
    size = 0
    async for chunk in client.iter_download(message, chunk_size=512 * 1024):
        size += len(chunk)
        h.update(chunk)
    return size, h.digest()


async def handle_cloud_chat_new_message(
    event: Any, ctx: IngestContext
) -> str | None:
    """Process one NewMessage event. Returns a short status string for tests
    / observability; None if the event was ignored before any work."""
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    msg = getattr(event, "message", None)
    if msg is None:
        return None

    # Cloud-membership check
    async with ctx.sessionmaker() as sess:
        result = await sess.execute(
            sa.select(Cloud).where(Cloud.chat_id == chat_id)
        )
        cloud_row = result.scalar_one_or_none()
    if cloud_row is None:
        return None

    # Already-processed check
    caption = (getattr(msg, "message", None) or "").strip()
    if caption.startswith(LC1_PREFIX):
        return "skip_existing_lc1"

    # Document-only in V1
    meta = _extract_document_meta(msg)
    if meta is None:
        logger.info(
            "ignoring non-document media in cloud %s message %s",
            chat_id,
            getattr(msg, "id", "?"),
        )
        return "skip_non_document"

    client = getattr(event, "client", None)
    if client is None:
        return None

    # Size cap (enforced before download to save bandwidth)
    if meta.size > ctx.settings.lc_max_file_bytes:
        with contextlib.suppress(Exception):
            await msg.delete()
        await _alert_admin(
            client,
            f"⚠️ LCloud: rejected {meta.original_name!r} "
            f"({meta.size:,} bytes > limit {ctx.settings.lc_max_file_bytes:,}) "
            f"in chat {chat_id}",
        )
        return "rejected_oversize"

    # Stream-download → sha256
    try:
        downloaded, sha = await _stream_sha256(client, msg)
    except Exception:
        logger.exception(
            "failed to compute sha256 for cloud %s message %s", chat_id, msg.id
        )
        return None

    if downloaded != meta.size:
        # Telegram reported one size, served another — refuse to sign a lie.
        logger.warning(
            "size mismatch on ingest: meta=%s actual=%s; refusing to persist",
            meta.size,
            downloaded,
        )
        return "size_mismatch"

    # Sign + edit caption + persist
    uploaded_at = int(time.time())
    payload = file_signature_payload(
        sha256_digest=sha,
        chat_id=chat_id,
        message_id=int(msg.id),
        owner_pubkey=bytes(ctx.signing_key.verify_key),
        uploaded_at_unix=uploaded_at,
    )
    signature = sign(ctx.signing_key, payload)
    new_caption = build_lc1_caption(
        sha256_digest=sha,
        signature=signature,
        owner_pubkey=bytes(ctx.signing_key.verify_key),
        uploaded_at_unix=uploaded_at,
    )

    try:
        await client.edit_message(chat_id, int(msg.id), new_caption)
    except Exception:
        logger.exception(
            "failed to edit caption for cloud %s message %s; not persisting row",
            chat_id,
            msg.id,
        )
        return None

    async with ctx.sessionmaker() as sess:
        sess.add(
            File(
                cloud_id=cloud_row.id,
                message_id=int(msg.id),
                owner_id=ctx.owner_id,
                original_name=meta.original_name,
                mime=meta.mime,
                size_bytes=meta.size,
                sha256=sha,
                signature=signature,
            )
        )
        await sess.commit()
    logger.info(
        "ingested cloud %s message %s name=%r size=%s",
        chat_id,
        msg.id,
        meta.original_name,
        meta.size,
    )
    return "ingested"


def register_userbot_handlers(
    client: TelegramClient, ctx: IngestContext
) -> None:
    """Attach the NewMessage handler to a connected, admin-authorized client."""

    async def _on_new_message(event: events.NewMessage.Event) -> None:
        try:
            await handle_cloud_chat_new_message(event, ctx)
        except Exception:
            logger.exception(
                "cloud-chat ingest handler crashed (chat=%s)", event.chat_id
            )

    client.add_event_handler(
        _on_new_message, events.NewMessage(incoming=True, outgoing=True)
    )
    logger.info("userbot NewMessage handler attached")
