"""Tests for `scan_dialogs_for_clouds` against a stub Telethon surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from nacl.signing import SigningKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from telethon.tl.functions.channels import GetFullChannelRequest

from lcloud.config import Settings
from lcloud.db.bootstrap import run_migrations_sync
from lcloud.db.models import Cloud, Owner
from lcloud.userbot.marker import build_marker
from lcloud.userbot.scan import scan_dialogs_for_clouds


# Telethon's get_peer_id() returns -1000000000000 - channel.id for megagroups
def _marked_id(channel_id: int) -> int:
    return -1_000_000_000_000 - channel_id


@dataclass
class FakeChannel:
    """Stand-in for telethon.tl.types.Channel, structurally compatible enough
    for `isinstance(entity, Channel)` we patch via subclass below."""

    id: int
    title: str = "X"
    megagroup: bool = True
    broadcast: bool = False
    access_hash: int = 0


@dataclass
class FakeDialog:
    entity: FakeChannel


@dataclass
class FakeChatFull:
    about: str


@dataclass
class FakeFullResult:
    full_chat: FakeChatFull


class FakeTelegramClient:
    def __init__(self, dialogs: list[FakeDialog], abouts: dict[int, str]) -> None:
        self._dialogs = dialogs
        self._abouts = abouts

    def iter_dialogs(self) -> AsyncIterator[FakeDialog]:
        async def _gen() -> AsyncIterator[FakeDialog]:
            for d in self._dialogs:
                yield d

        return _gen()

    async def __call__(self, request: Any) -> Any:
        if isinstance(request, GetFullChannelRequest):
            channel = request.channel
            return FakeFullResult(
                full_chat=FakeChatFull(about=self._abouts.get(channel.id, ""))
            )
        raise NotImplementedError(type(request).__name__)


@pytest.fixture
async def db_setup(tmp_path: Path) -> Any:
    s = Settings(
        _env_file=None,
        lc_data_dir=tmp_path,
        lc_db_url=f"sqlite+aiosqlite:///{tmp_path / 'lcloud.db'}",
    )
    run_migrations_sync(s)
    # scan_dialogs_for_clouds → mtproto_call → get_mtproto_limiter()
    # Tests need an initialised limiter (high rate so it never gates).
    from lcloud.workers import init_mtproto_limiter, reset_mtproto_limiter

    reset_mtproto_limiter()
    init_mtproto_limiter(
        Settings(_env_file=None, lc_mtproto_rate_per_sec=1000.0, lc_mtproto_burst=100)
    )

    eng = create_async_engine(s.lc_db_url, future=True)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    pubkey = b"\x01" * 32
    async with sm() as sess:
        sess.add(Owner(pubkey=pubkey, label="admin", role="admin"))
        await sess.commit()
        owner = (
            await sess.execute(sa.select(Owner).where(Owner.role == "admin"))
        ).scalar_one()
        owner_id = owner.id
    try:
        yield sm, owner_id, eng
    finally:
        reset_mtproto_limiter()


# We need entities to satisfy isinstance(..., Channel). Patch Channel to FakeChannel.
@pytest.fixture
def patch_channel_isinstance(monkeypatch: pytest.MonkeyPatch) -> None:
    import lcloud.userbot.scan as scan_mod

    monkeypatch.setattr(scan_mod, "Channel", FakeChannel)
    # get_peer_id for megagroup
    monkeypatch.setattr(scan_mod, "get_peer_id", lambda e: _marked_id(e.id))


@pytest.mark.asyncio
async def test_scan_picks_up_valid_marker(
    db_setup: Any, patch_channel_isinstance: None
) -> None:
    sm, owner_id, eng = db_setup
    sk = SigningKey.generate()

    # Two cloud chats with valid markers
    ch1 = FakeChannel(id=111, title="Photos", megagroup=True)
    ch2 = FakeChannel(id=222, title="Docs", megagroup=True)
    abouts = {
        111: build_marker(signing_key=sk, chat_id=_marked_id(111)),
        222: build_marker(signing_key=sk, chat_id=_marked_id(222)),
    }
    # A non-cloud chat: megagroup, no marker
    ch3 = FakeChannel(id=333, title="Friends", megagroup=True)
    abouts[333] = "just a regular group description"
    # A non-megagroup channel (broadcast); should be skipped
    ch4 = FakeChannel(id=444, title="News", megagroup=False, broadcast=True)
    abouts[444] = build_marker(signing_key=sk, chat_id=_marked_id(444))

    fake = FakeTelegramClient(
        dialogs=[FakeDialog(ch1), FakeDialog(ch2), FakeDialog(ch3), FakeDialog(ch4)],
        abouts=abouts,
    )

    n = await scan_dialogs_for_clouds(
        fake,  # type: ignore[arg-type]
        sessionmaker=sm,
        owner_id=owner_id,
        expected_pubkey=bytes(sk.verify_key),
    )
    assert n == 2

    async with sm() as sess:
        rows = (
            await sess.execute(sa.select(Cloud).order_by(Cloud.chat_id))
        ).scalars().all()
        ids = sorted(r.chat_id for r in rows)
    assert ids == sorted([_marked_id(111), _marked_id(222)])
    await eng.dispose()


@pytest.mark.asyncio
async def test_scan_rejects_marker_with_wrong_pubkey(
    db_setup: Any, patch_channel_isinstance: None
) -> None:
    sm, owner_id, eng = db_setup
    real_sk = SigningKey.generate()
    impostor_sk = SigningKey.generate()

    ch = FakeChannel(id=111, title="Bad", megagroup=True)
    fake = FakeTelegramClient(
        dialogs=[FakeDialog(ch)],
        abouts={111: build_marker(signing_key=impostor_sk, chat_id=_marked_id(111))},
    )

    n = await scan_dialogs_for_clouds(
        fake,  # type: ignore[arg-type]
        sessionmaker=sm,
        owner_id=owner_id,
        expected_pubkey=bytes(real_sk.verify_key),
    )
    assert n == 0
    async with sm() as sess:
        rows = (await sess.execute(sa.select(Cloud))).scalars().all()
    assert rows == []
    await eng.dispose()


@pytest.mark.asyncio
async def test_scan_idempotent_updates_name(
    db_setup: Any, patch_channel_isinstance: None
) -> None:
    sm, owner_id, eng = db_setup
    sk = SigningKey.generate()
    pub = bytes(sk.verify_key)
    cid = _marked_id(111)

    # First scan with title "Old"
    ch = FakeChannel(id=111, title="Old", megagroup=True)
    abouts = {111: build_marker(signing_key=sk, chat_id=cid)}
    fake = FakeTelegramClient(dialogs=[FakeDialog(ch)], abouts=abouts)
    await scan_dialogs_for_clouds(
        fake, sessionmaker=sm, owner_id=owner_id, expected_pubkey=pub  # type: ignore[arg-type]
    )

    # Second scan with title "New" — must update, not duplicate
    ch.title = "New"
    fake2 = FakeTelegramClient(dialogs=[FakeDialog(ch)], abouts=abouts)
    n = await scan_dialogs_for_clouds(
        fake2, sessionmaker=sm, owner_id=owner_id, expected_pubkey=pub  # type: ignore[arg-type]
    )
    assert n == 1
    async with sm() as sess:
        rows = (await sess.execute(sa.select(Cloud))).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "New"
    await eng.dispose()
