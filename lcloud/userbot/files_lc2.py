"""LC2 (client-signed) variant of `upload_file_to_cloud`.

Only difference from V1's `upload_file_to_cloud`:
- Caption is built from **client-supplied** (sig, sha256, ts, pubkey)
  instead of being server-side signed at upload time.
- The server has already verified the signature against the client's
  pubkey before this helper is called; this helper just writes the bytes
  to TG and stamps the caption.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename

from lcloud.crypto.lc2 import Lc2Payload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Lc2UploadResult:
    message_id: int
    caption: str  # the LC2:{...} string
    payload: Lc2Payload  # convenience: parsed back


async def upload_file_lc2(
    client: TelegramClient,
    *,
    chat_id: int,
    file_path: Path,
    original_name: str,
    payload: Lc2Payload,
) -> Lc2UploadResult:
    """Upload `file_path` to `chat_id` as a document and stamp the LC2 caption."""
    entity = await client.get_entity(chat_id)
    msg = await client.send_file(
        entity,
        file=str(file_path),
        force_document=True,
        attributes=[DocumentAttributeFilename(file_name=original_name)],
    )
    message_id: int = int(msg.id)
    caption = payload.to_caption()
    await client.edit_message(entity, message_id, caption)
    logger.info(
        "uploaded LC2 file chat=%s message_id=%s name=%r owner=%s...",
        chat_id,
        message_id,
        original_name,
        payload.pubkey.hex()[:16],
    )
    return Lc2UploadResult(
        message_id=message_id,
        caption=caption,
        payload=payload,
    )


__all__ = ["Lc2UploadResult", "upload_file_lc2"]
