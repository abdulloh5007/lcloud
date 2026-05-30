"""Tests for Saved-Messages slash commands: /revoke /status /help."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lcloud.config import Settings
from lcloud.db.bootstrap import run_migrations_sync
from lcloud.db.models import AuthState, Cloud, File, Owner
from lcloud.userbot.commands import (
    CommandContext,
    handle_saved_messages_command,
)


@dataclass
class FakeMessage:
    out: bool
    message: str
    id: int = 1


class FakeSavedClient:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def send_message(self, peer: str, text: str, **_kwargs: object) -> None:
        if peer == "me":
            self.replies.append(text)


@dataclass
class FakeEvent:
    message: FakeMessage
    client: FakeSavedClient = field(default_factory=FakeSavedClient)


@pytest.fixture
async def cmd_ctx(tmp_path: Path) -> AsyncIterator[Any]:
    s = Settings(
        _env_file=None,
        lc_data_dir=tmp_path,
        lc_db_url=f"sqlite+aiosqlite:///{tmp_path / 'lcloud.db'}",
    )
    run_migrations_sync(s)
    eng = create_async_engine(s.lc_db_url, future=True)
    sm: async_sessionmaker[Any] = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as sess:
        sess.add(Owner(pubkey=b"\x01" * 32, label="admin", role="admin"))
        await sess.commit()
        owner = (
            await sess.execute(sa.select(Owner).where(Owner.role == "admin"))
        ).scalar_one()
        sess.add(AuthState(owner_id=owner.id, epoch=1))
        await sess.commit()
        owner_id = owner.id
    from nacl.signing import SigningKey

    ctx = CommandContext(
        sessionmaker=sm,
        owner_id=owner_id,
        signing_key=SigningKey.generate(),
        settings=s,
    )
    try:
        yield ctx, sm
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_help_returns_help_text(cmd_ctx: Any) -> None:
    ctx, _ = cmd_ctx
    event = FakeEvent(message=FakeMessage(out=True, message="/help"))
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None
    # New help text covers all 7 base commands
    for keyword in (
        "/revoke",
        "/status",
        "/clouds",
        "/createcloud",
        "/connect",
        "/disconnect",
        "/lc_connect",
    ):
        assert keyword in reply, f"{keyword!r} missing from help"
    assert event.client.replies == [reply]


@pytest.mark.asyncio
async def test_status_counts_clouds_files_size(cmd_ctx: Any) -> None:
    ctx, sm = cmd_ctx
    # Seed: 2 clouds, 3 files (one deleted) on cloud 1
    async with sm() as sess:
        c1 = Cloud(chat_id=-1, owner_id=ctx.owner_id, name="A")
        c2 = Cloud(chat_id=-2, owner_id=ctx.owner_id, name="B")
        sess.add_all([c1, c2])
        await sess.commit()
        await sess.refresh(c1)
        sess.add_all(
            [
                File(
                    cloud_id=c1.id, message_id=1, owner_id=ctx.owner_id,
                    original_name="a.txt", mime="text/plain",
                    size_bytes=1024, sha256=hashlib.sha256(b"a").digest(),
                    signature=b"\x00" * 64,
                ),
                File(
                    cloud_id=c1.id, message_id=2, owner_id=ctx.owner_id,
                    original_name="b.txt", mime="text/plain",
                    size_bytes=2048, sha256=hashlib.sha256(b"b").digest(),
                    signature=b"\x00" * 64,
                ),
                File(
                    cloud_id=c1.id, message_id=3, owner_id=ctx.owner_id,
                    original_name="c.txt", mime="text/plain",
                    size_bytes=999_999, sha256=hashlib.sha256(b"c").digest(),
                    signature=b"\x00" * 64,
                    deleted_at=sa.func.now(),
                ),
            ]
        )
        await sess.commit()

    event = FakeEvent(message=FakeMessage(out=True, message="/status"))
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None
    assert "Clouds: 2" in reply
    assert "Files: 2" in reply  # the soft-deleted one excluded
    # Total = 1024 + 2048 = 3072 bytes (excludes deleted)
    assert "3,072 bytes" in reply or "3072 bytes" in reply


@pytest.mark.asyncio
async def test_revoke_bumps_auth_epoch(cmd_ctx: Any) -> None:
    ctx, sm = cmd_ctx
    event = FakeEvent(message=FakeMessage(out=True, message="/revoke"))
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None
    assert "auth_epoch=2" in reply

    # DB epoch advanced from 1 -> 2
    async with sm() as sess:
        epoch = (
            await sess.execute(
                sa.select(AuthState.epoch).where(AuthState.owner_id == ctx.owner_id)
            )
        ).scalar_one()
    assert epoch == 2

    # Second /revoke advances again
    event2 = FakeEvent(message=FakeMessage(out=True, message="/revoke"))
    reply = await handle_saved_messages_command(event2, ctx)
    assert reply is not None
    assert "auth_epoch=3" in reply


@pytest.mark.asyncio
async def test_unknown_command_ignored(cmd_ctx: Any) -> None:
    ctx, _ = cmd_ctx
    event = FakeEvent(message=FakeMessage(out=True, message="/wat"))
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is None
    assert event.client.replies == []


@pytest.mark.asyncio
async def test_non_command_text_ignored(cmd_ctx: Any) -> None:
    ctx, _ = cmd_ctx
    event = FakeEvent(message=FakeMessage(out=True, message="just a thought"))
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is None


@pytest.mark.asyncio
async def test_incoming_message_not_processed(cmd_ctx: Any) -> None:
    """Defence-in-depth: even if Telethon mis-delivers, refuse non-self."""
    ctx, _ = cmd_ctx
    event = FakeEvent(message=FakeMessage(out=False, message="/revoke"))
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is None
