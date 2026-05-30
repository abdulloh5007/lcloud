"""Tests for the cloud-chat NewMessage ingest handler.

Uses a hand-rolled fake event + fake client (no real Telethon network).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from nacl.signing import SigningKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from telethon.tl.types import DocumentAttributeFilename

from lcloud.config import Settings
from lcloud.crypto.sign import file_signature_payload, verify
from lcloud.db.bootstrap import run_migrations_sync
from lcloud.db.models import Cloud, File, Owner
from lcloud.userbot.handlers import (
    IngestContext,
    handle_cloud_chat_new_message,
)
from lcloud.userbot.lc1 import build_lc1_caption, parse_lc1_caption

# ------------------------------------------------------------------ fakes


@dataclass
class FakeDocument:
    size: int
    mime_type: str
    attributes: list[Any] = field(default_factory=list)


@dataclass
class FakeMessage:
    id: int
    chat_id: int
    document: FakeDocument | None = None
    message: str = ""  # caption (Telethon naming)
    deleted: bool = False

    async def delete(self) -> None:
        self.deleted = True


class FakeIngestClient:
    def __init__(self, *, payload: bytes, edit_should_fail: bool = False) -> None:
        self._payload = payload
        self.captions: dict[int, str] = {}
        self.saved_alerts: list[str] = []
        self._edit_should_fail = edit_should_fail

    def iter_download(
        self, message: FakeMessage, *, chunk_size: int = 0
    ) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            data = self._payload
            step = chunk_size or 64 * 1024
            for i in range(0, len(data), step):
                yield data[i : i + step]

        return _gen()

    async def edit_message(
        self, chat_id: int, message_id: int, caption: str
    ) -> None:
        if self._edit_should_fail:
            raise RuntimeError("edit failed")
        self.captions[message_id] = caption

    async def send_message(self, peer: str, text: str) -> None:
        if peer == "me":
            self.saved_alerts.append(text)


@dataclass
class FakeEvent:
    chat_id: int
    message: FakeMessage
    client: FakeIngestClient


# ------------------------------------------------------------------ db fixture


@pytest.fixture
async def ingest_ctx(tmp_path: Path) -> Any:
    s = Settings(
        _env_file=None,
        lc_data_dir=tmp_path,
        lc_db_url=f"sqlite+aiosqlite:///{tmp_path / 'lcloud.db'}",
        lc_max_file_bytes=10_000,
    )
    run_migrations_sync(s)

    eng = create_async_engine(s.lc_db_url, future=True)
    sm: async_sessionmaker[Any] = async_sessionmaker(eng, expire_on_commit=False)

    sk = SigningKey.generate()
    pubkey = bytes(sk.verify_key)

    async with sm() as sess:
        sess.add(Owner(pubkey=pubkey, label="admin", role="admin"))
        await sess.commit()
        owner = (
            await sess.execute(sa.select(Owner).where(Owner.role == "admin"))
        ).scalar_one()
        # Pre-seed a cloud row so the chat is "tracked"
        sess.add(
            Cloud(
                chat_id=-1_001_111_111_111,
                owner_id=owner.id,
                name="Test Cloud",
                about="LCLOUD1:fake",
            )
        )
        await sess.commit()
        owner_id = owner.id

    ctx = IngestContext(sessionmaker=sm, signing_key=sk, settings=s, owner_id=owner_id)
    try:
        yield ctx, sm, eng
    finally:
        await eng.dispose()


# ------------------------------------------------------------------ tests


@pytest.mark.asyncio
async def test_ingest_happy_path_signs_and_persists(ingest_ctx: Any) -> None:
    ctx, sm, _eng = ingest_ctx
    payload = b"hello cloud\n" * 50  # 600 bytes
    sha = hashlib.sha256(payload).digest()
    msg = FakeMessage(
        id=42,
        chat_id=-1_001_111_111_111,
        document=FakeDocument(
            size=len(payload),
            mime_type="text/plain",
            attributes=[DocumentAttributeFilename(file_name="dropped.txt")],
        ),
        message="",  # no caption
    )
    client = FakeIngestClient(payload=payload)
    event = FakeEvent(chat_id=msg.chat_id, message=msg, client=client)

    result = await handle_cloud_chat_new_message(event, ctx)
    assert result == "ingested"

    # Caption was edited to LC1 with verifiable signature
    assert 42 in client.captions
    parsed = parse_lc1_caption(client.captions[42])
    assert parsed is not None
    assert parsed.sha256_digest == sha
    assert parsed.owner_pubkey == bytes(ctx.signing_key.verify_key)

    canonical = file_signature_payload(
        sha256_digest=sha,
        chat_id=msg.chat_id,
        message_id=42,
        owner_pubkey=parsed.owner_pubkey,
        uploaded_at_unix=parsed.uploaded_at_unix,
    )
    assert verify(ctx.signing_key.verify_key, parsed.signature, canonical)

    # DB row persisted
    async with sm() as sess:
        rows = (await sess.execute(sa.select(File))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.original_name == "dropped.txt"
    assert row.size_bytes == len(payload)
    assert row.sha256 == sha
    assert row.message_id == 42
    assert row.deleted_at is None


@pytest.mark.asyncio
async def test_ingest_skips_existing_lc1_caption(ingest_ctx: Any) -> None:
    ctx, sm, _ = ingest_ctx
    pre_caption = build_lc1_caption(
        sha256_digest=b"\x00" * 32,
        signature=b"\x00" * 64,
        owner_pubkey=b"\x00" * 32,
        uploaded_at_unix=1,
    )
    msg = FakeMessage(
        id=1,
        chat_id=-1_001_111_111_111,
        document=FakeDocument(size=10, mime_type="text/plain"),
        message=pre_caption,  # already has LC1
    )
    client = FakeIngestClient(payload=b"xxxxxxxxxx")
    event = FakeEvent(chat_id=msg.chat_id, message=msg, client=client)

    result = await handle_cloud_chat_new_message(event, ctx)
    assert result == "skip_existing_lc1"
    async with sm() as sess:
        rows = (await sess.execute(sa.select(File))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_ingest_skips_non_cloud_chat(ingest_ctx: Any) -> None:
    ctx, sm, _ = ingest_ctx
    msg = FakeMessage(
        id=1,
        chat_id=-9999999,  # NOT a tracked cloud
        document=FakeDocument(size=10, mime_type="text/plain"),
    )
    client = FakeIngestClient(payload=b"xxxxxxxxxx")
    event = FakeEvent(chat_id=msg.chat_id, message=msg, client=client)

    result = await handle_cloud_chat_new_message(event, ctx)
    assert result is None
    async with sm() as sess:
        rows = (await sess.execute(sa.select(File))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_ingest_rejects_oversize_with_alert(ingest_ctx: Any) -> None:
    ctx, sm, _ = ingest_ctx
    msg = FakeMessage(
        id=7,
        chat_id=-1_001_111_111_111,
        document=FakeDocument(
            size=100_000,  # > 10_000 limit
            mime_type="application/octet-stream",
            attributes=[DocumentAttributeFilename(file_name="huge.bin")],
        ),
    )
    client = FakeIngestClient(payload=b"X" * 100_000)
    event = FakeEvent(chat_id=msg.chat_id, message=msg, client=client)

    result = await handle_cloud_chat_new_message(event, ctx)
    assert result == "rejected_oversize"
    assert msg.deleted is True
    assert client.saved_alerts and "huge.bin" in client.saved_alerts[0]
    async with sm() as sess:
        rows = (await sess.execute(sa.select(File))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_ingest_skips_non_document_media(ingest_ctx: Any) -> None:
    ctx, _, _ = ingest_ctx
    # Photo / sticker / etc — `document=None`
    msg = FakeMessage(id=1, chat_id=-1_001_111_111_111, document=None)
    msg.message = ""
    client = FakeIngestClient(payload=b"")
    event = FakeEvent(chat_id=msg.chat_id, message=msg, client=client)
    result = await handle_cloud_chat_new_message(event, ctx)
    assert result == "skip_non_document"


@pytest.mark.asyncio
async def test_ingest_size_mismatch_refuses_to_persist(ingest_ctx: Any) -> None:
    """If Telegram says size=100 but the stream yields 50 bytes, refuse."""
    ctx, sm, _ = ingest_ctx
    payload = b"X" * 50
    msg = FakeMessage(
        id=1,
        chat_id=-1_001_111_111_111,
        document=FakeDocument(
            size=100,  # claims 100
            mime_type="application/octet-stream",
            attributes=[DocumentAttributeFilename(file_name="lying.bin")],
        ),
    )
    client = FakeIngestClient(payload=payload)  # only delivers 50
    event = FakeEvent(chat_id=msg.chat_id, message=msg, client=client)

    result = await handle_cloud_chat_new_message(event, ctx)
    assert result == "size_mismatch"
    async with sm() as sess:
        rows = (await sess.execute(sa.select(File))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_ingest_caption_edit_failure_does_not_persist(
    ingest_ctx: Any,
) -> None:
    ctx, sm, _ = ingest_ctx
    payload = b"hi" * 100
    msg = FakeMessage(
        id=11,
        chat_id=-1_001_111_111_111,
        document=FakeDocument(
            size=len(payload),
            mime_type="text/plain",
            attributes=[DocumentAttributeFilename(file_name="x.txt")],
        ),
    )
    client = FakeIngestClient(payload=payload, edit_should_fail=True)
    event = FakeEvent(chat_id=msg.chat_id, message=msg, client=client)

    result = await handle_cloud_chat_new_message(event, ctx)
    assert result is None
    async with sm() as sess:
        rows = (await sess.execute(sa.select(File))).scalars().all()
    assert rows == []
