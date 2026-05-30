"""Tests for V2 admin bootstrap-seed-via-Telegram delivery."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa

from lcloud.auth.seed import derive_keypair, is_valid_mnemonic
from lcloud.config import get_settings
from lcloud.db import base as base_mod
from lcloud.db.base import get_sessionmaker, init_engine
from lcloud.db.bootstrap import run_migrations
from lcloud.db.models import User
from lcloud.userbot.admin_bootstrap import (
    SAVED_MESSAGES_PEER,
    ensure_admin_seed_delivered,
)


class FakeSavedMessagesClient:
    """Minimal Telethon-like client capturing send_message calls."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    async def send_message(
        self, peer: str, text: str, **_kwargs: object
    ) -> object:
        if self.fail:
            raise RuntimeError("send_message exploded")
        self.sent.append((peer, text))
        return object()


@pytest_asyncio.fixture
async def db_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    """Fresh sqlite + migrations for each test (proper isolation)."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("LC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LC_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "x")

    get_settings.cache_clear()
    # Reset the module-level engine/sessionmaker so init_engine actually
    # rebuilds against the new tmp_path DB.
    base_mod._engine = None
    base_mod._sessionmaker = None

    settings = get_settings()
    init_engine(settings)
    await run_migrations(settings)
    yield
    if base_mod._engine is not None:
        await base_mod._engine.dispose()
    base_mod._engine = None
    base_mod._sessionmaker = None
    get_settings.cache_clear()


async def test_bootstrap_creates_admin_and_sends_seed(db_session: None) -> None:
    client = FakeSavedMessagesClient()
    sm = get_sessionmaker()

    sent = await ensure_admin_seed_delivered(
        client=client,
        sessionmaker=sm,
        public_base_url="https://example.test",
    )
    assert sent is True

    # Saved Messages got exactly one message addressed to "me"
    assert len(client.sent) == 1
    peer, text = client.sent[0]
    assert peer == SAVED_MESSAGES_PEER
    assert "https://example.test" in text
    assert "seed-фраза" in text.lower()

    # Extract the 12 words (between fenced ```...```) and verify it's a valid
    # BIP39 mnemonic that derives the same pubkey we stored on the User row.
    match = re.search(r"```\s*([a-z ]+?)\s*```", text)
    assert match is not None, f"no fenced mnemonic block in: {text!r}"
    mnemonic = match.group(1).strip()
    assert len(mnemonic.split()) == 12
    assert is_valid_mnemonic(mnemonic)

    derived_pub = derive_keypair(mnemonic).pubkey
    async with sm() as sess:
        admin = (
            await sess.execute(sa.select(User).where(User.role == "admin"))
        ).scalar_one()
    assert admin.pubkey == derived_pub
    assert admin.role == "admin"
    # Admin should have a much higher quota than regular users
    assert admin.storage_quota_bytes >= 100 * 1024**3


async def test_bootstrap_idempotent_returns_false_second_time(db_session: None) -> None:
    sm = get_sessionmaker()
    client1 = FakeSavedMessagesClient()
    sent1 = await ensure_admin_seed_delivered(
        client=client1, sessionmaker=sm, public_base_url="https://x"
    )
    assert sent1 is True

    client2 = FakeSavedMessagesClient()
    sent2 = await ensure_admin_seed_delivered(
        client=client2, sessionmaker=sm, public_base_url="https://x"
    )
    assert sent2 is False
    # Second call must NOT have sent anything
    assert client2.sent == []

    # Still exactly one admin in DB
    async with sm() as sess:
        admins = (
            await sess.execute(sa.select(User).where(User.role == "admin"))
        ).scalars().all()
    assert len(admins) == 1


async def test_bootstrap_propagates_send_failure(db_session: None) -> None:
    """If TG send_message fails, the admin row stays in DB so the operator can
    look it up by removing the row and re-running. The exception propagates."""
    sm = get_sessionmaker()
    client = FakeSavedMessagesClient(fail=True)

    with pytest.raises(RuntimeError, match="exploded"):
        await ensure_admin_seed_delivered(
            client=client, sessionmaker=sm, public_base_url="https://x"
        )

    async with sm() as sess:
        admins = (
            await sess.execute(sa.select(User).where(User.role == "admin"))
        ).scalars().all()
    # Row WAS persisted before the send failed
    assert len(admins) == 1
