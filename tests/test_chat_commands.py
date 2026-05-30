"""Tests for the new cloud-management Saved-Messages commands and the
in-chat /lc_connect /lc_disconnect handler."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from nacl.signing import SigningKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from telethon.tl.functions.messages import EditChatAboutRequest

from lcloud.config import Settings
from lcloud.db.bootstrap import run_migrations_sync
from lcloud.db.models import AuthState, Cloud, Owner
from lcloud.userbot.commands import (
    CommandContext,
    handle_saved_messages_command,
)
from lcloud.userbot.inchat import InChatContext, handle_in_chat_command


def _marked(channel_id: int) -> int:
    """Replicate telethon.utils.get_peer_id for megagroup."""
    return -1_000_000_000_000 - channel_id


@pytest.fixture(autouse=True)
def _patch_telethon_internals(monkeypatch: pytest.MonkeyPatch) -> None:
    """`telethon.utils.get_peer_id` and `isinstance(.., Channel)` both reject
    our ad-hoc fakes. Patch the symbols at every call site."""
    from lcloud.userbot import clouds as clouds_mod
    from lcloud.userbot import commands as cmds_mod
    from lcloud.userbot import inchat as inchat_mod

    def fake_get_peer_id(entity: Any) -> int:
        return _marked(entity.id)

    monkeypatch.setattr(clouds_mod, "get_peer_id", fake_get_peer_id)
    monkeypatch.setattr(inchat_mod, "get_peer_id", fake_get_peer_id)
    monkeypatch.setattr(cmds_mod, "get_peer_id", fake_get_peer_id)
    # `isinstance(chat, Channel)` checks: substitute the runtime symbol so
    # FakeChannel passes the type check.
    monkeypatch.setattr(clouds_mod, "Channel", FakeChannel)
    monkeypatch.setattr(inchat_mod, "Channel", FakeChannel)
    monkeypatch.setattr(cmds_mod, "Channel", FakeChannel)


# ----- shared mock surface --------------------------------------------------


@dataclass
class FakeChannel:
    id: int
    title: str = "X"
    megagroup: bool = True
    broadcast: bool = False
    access_hash: int = 0
    username: str | None = None


@dataclass
class FakeMessage:
    out: bool
    message: str
    id: int = 1
    deleted: bool = False

    async def delete(self) -> None:
        self.deleted = True


class FakeClient:
    """Telethon stand-in covering the bits commands.py / inchat.py touch."""

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.next_chan_id = 100
        self.entities: dict[int | str, FakeChannel] = {}
        self.create_calls: list[str] = []
        self.edit_about_calls: list[tuple[int, str]] = []

    async def send_message(
        self, peer: str, text: str, **_kwargs: Any
    ) -> None:
        if peer == "me":
            self.replies.append(text)

    async def get_entity(self, target: Any) -> FakeChannel:
        if target in self.entities:
            return self.entities[target]
        raise ValueError(f"no entity for {target!r}")

    async def __call__(self, request: Any) -> Any:
        # `mtproto_call` ultimately calls `client(request)`.
        if isinstance(request, EditChatAboutRequest):
            channel: FakeChannel = request.peer  # type: ignore[assignment]
            self.edit_about_calls.append((channel.id, request.about))
            return True
        # CreateChannelRequest
        if request.__class__.__name__ == "CreateChannelRequest":
            title = getattr(request, "title", "untitled")
            self.create_calls.append(title)
            ch = FakeChannel(id=self.next_chan_id, title=title, megagroup=True)
            self.next_chan_id += 1

            class Result:
                chats: Any = [ch]  # noqa: RUF012  — test stub

            return Result()
        raise NotImplementedError(type(request).__name__)


@dataclass
class FakeEvent:
    message: FakeMessage
    client: FakeClient
    chat_id: int = 0
    is_private: bool = False
    chat: FakeChannel | None = None

    async def get_chat(self) -> Any:
        return self.chat


# ----- fixtures -------------------------------------------------------------


@pytest.fixture
async def saved_ctx(tmp_path: Path) -> AsyncIterator[Any]:
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
        oid = owner.id

    # Init mtproto limiter so mtproto_call doesn't blow up
    from lcloud.workers import init_mtproto_limiter, reset_mtproto_limiter

    reset_mtproto_limiter()
    init_mtproto_limiter(
        Settings(_env_file=None, lc_mtproto_rate_per_sec=1000.0, lc_mtproto_burst=100)
    )

    ctx = CommandContext(
        sessionmaker=sm, owner_id=oid, signing_key=SigningKey.generate(), settings=s
    )
    try:
        yield ctx, sm
    finally:
        await eng.dispose()
        reset_mtproto_limiter()


@pytest.fixture
async def inchat_ctx(tmp_path: Path) -> AsyncIterator[Any]:
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
        oid = (
            await sess.execute(sa.select(Owner).where(Owner.role == "admin"))
        ).scalar_one().id

    from lcloud.workers import init_mtproto_limiter, reset_mtproto_limiter

    reset_mtproto_limiter()
    init_mtproto_limiter(
        Settings(_env_file=None, lc_mtproto_rate_per_sec=1000.0, lc_mtproto_burst=100)
    )

    ctx = InChatContext(
        sessionmaker=sm, owner_id=oid, signing_key=SigningKey.generate()
    )
    try:
        yield ctx, sm
    finally:
        await eng.dispose()
        reset_mtproto_limiter()


# ============================================================================
# Saved-Messages: /clouds /createcloud /connect /disconnect
# ============================================================================


@pytest.mark.asyncio
async def test_clouds_lists_empty_initially(saved_ctx: Any) -> None:
    ctx, _ = saved_ctx
    event = FakeEvent(
        message=FakeMessage(out=True, message="/clouds"),
        client=FakeClient(),
    )
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None
    assert "No clouds" in reply or "Use /createcloud" in reply


@pytest.mark.asyncio
async def test_createcloud_creates_chat_and_persists(saved_ctx: Any) -> None:
    ctx, sm = saved_ctx
    event = FakeEvent(
        message=FakeMessage(out=True, message="/createcloud My Photos"),
        client=FakeClient(),
    )
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None and "Created cloud" in reply
    assert event.client.create_calls == ["My Photos"]
    # Marker was edited (one EditChatAboutRequest call)
    assert len(event.client.edit_about_calls) == 1
    chat_id, about = event.client.edit_about_calls[0]
    assert about.startswith("LCLOUD1:")

    async with sm() as sess:
        rows = (await sess.execute(sa.select(Cloud))).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "My Photos"


@pytest.mark.asyncio
async def test_createcloud_rejects_empty_name(saved_ctx: Any) -> None:
    ctx, _ = saved_ctx
    event = FakeEvent(
        message=FakeMessage(out=True, message="/createcloud"),
        client=FakeClient(),
    )
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None and "usage:" in reply


@pytest.mark.asyncio
async def test_connect_resolves_existing_supergroup(saved_ctx: Any) -> None:
    ctx, sm = saved_ctx
    client = FakeClient()
    chan = FakeChannel(id=42, title="Some Group", megagroup=True, username="some_grp")
    client.entities["@some_grp"] = chan
    event = FakeEvent(
        message=FakeMessage(out=True, message="/connect @some_grp"),
        client=client,
    )
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None and "Connected" in reply
    async with sm() as sess:
        rows = (await sess.execute(sa.select(Cloud))).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "Some Group"


@pytest.mark.asyncio
async def test_connect_rejects_non_supergroup(saved_ctx: Any) -> None:
    ctx, sm = saved_ctx
    client = FakeClient()
    client.entities["@private"] = FakeChannel(
        id=99, title="Private", megagroup=False, broadcast=True
    )
    event = FakeEvent(
        message=FakeMessage(out=True, message="/connect @private"),
        client=client,
    )
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None and "couldn't resolve" in reply
    async with sm() as sess:
        assert (await sess.execute(sa.select(Cloud))).scalars().all() == []


@pytest.mark.asyncio
async def test_connect_already_connected_returns_info(saved_ctx: Any) -> None:
    ctx, sm = saved_ctx
    client = FakeClient()
    chan = FakeChannel(id=77, title="Pre", megagroup=True, username="pre")
    client.entities["@pre"] = chan
    marked = _marked(chan.id)
    async with sm() as sess:
        sess.add(
            Cloud(
                chat_id=marked,
                owner_id=ctx.owner_id,
                name="Pre",
                about="LCLOUD1:fake",
            )
        )
        await sess.commit()

    event = FakeEvent(
        message=FakeMessage(out=True, message="/connect @pre"),
        client=client,
    )
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None and "Already connected" in reply


@pytest.mark.asyncio
async def test_disconnect_drops_cloud_row(saved_ctx: Any) -> None:
    ctx, sm = saved_ctx
    client = FakeClient()
    chan = FakeChannel(id=10, title="Trash", megagroup=True)
    client.entities[-1_000_000_000_010] = chan  # Telethon marked id

    async with sm() as sess:
        row = Cloud(
            chat_id=-1_000_000_000_010,
            owner_id=ctx.owner_id,
            name="Trash",
            about="LCLOUD1:fake",
        )
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)
        cid = row.id

    event = FakeEvent(
        message=FakeMessage(out=True, message=f"/disconnect {cid}"),
        client=client,
    )
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None and "Disconnected" in reply
    async with sm() as sess:
        rows = (await sess.execute(sa.select(Cloud))).scalars().all()
    assert rows == []
    # marker clear was attempted
    assert any(call[1] == "" for call in client.edit_about_calls)


@pytest.mark.asyncio
async def test_disconnect_unknown_id(saved_ctx: Any) -> None:
    ctx, _ = saved_ctx
    event = FakeEvent(
        message=FakeMessage(out=True, message="/disconnect 9999"),
        client=FakeClient(),
    )
    reply = await handle_saved_messages_command(event, ctx)
    assert reply is not None and "not found" in reply


# ============================================================================
# In-chat: /lc_connect /lc_disconnect
# ============================================================================


@pytest.mark.asyncio
async def test_lc_connect_marks_chat_and_persists(inchat_ctx: Any) -> None:
    ctx, sm = inchat_ctx
    client = FakeClient()
    chan = FakeChannel(id=5, title="DropZone", megagroup=True)
    event = FakeEvent(
        message=FakeMessage(out=True, message="/lc_connect"),
        client=client,
        chat=chan,
        is_private=False,
    )
    result = await handle_in_chat_command(event, ctx)
    assert result == "connected"
    assert event.message.deleted is True
    async with sm() as sess:
        rows = (await sess.execute(sa.select(Cloud))).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "DropZone"
    # Confirmation went to Saved Messages
    assert any("Connected" in r for r in client.replies)


@pytest.mark.asyncio
async def test_lc_connect_with_custom_name(inchat_ctx: Any) -> None:
    ctx, sm = inchat_ctx
    client = FakeClient()
    chan = FakeChannel(id=6, title="raw_title", megagroup=True)
    event = FakeEvent(
        message=FakeMessage(out=True, message="/lc_connect Pretty Name"),
        client=client,
        chat=chan,
    )
    result = await handle_in_chat_command(event, ctx)
    assert result == "connected"
    async with sm() as sess:
        rows = (await sess.execute(sa.select(Cloud))).scalars().all()
    assert rows[0].name == "Pretty Name"


@pytest.mark.asyncio
async def test_lc_connect_rejects_non_supergroup(inchat_ctx: Any) -> None:
    ctx, sm = inchat_ctx
    client = FakeClient()
    chan = FakeChannel(id=7, title="news", megagroup=False, broadcast=True)
    event = FakeEvent(
        message=FakeMessage(out=True, message="/lc_connect"),
        client=client,
        chat=chan,
    )
    result = await handle_in_chat_command(event, ctx)
    assert result == "skip_not_supergroup"
    assert event.message.deleted is True
    async with sm() as sess:
        assert (await sess.execute(sa.select(Cloud))).scalars().all() == []


@pytest.mark.asyncio
async def test_lc_connect_skips_private_chat(inchat_ctx: Any) -> None:
    ctx, _ = inchat_ctx
    client = FakeClient()
    event = FakeEvent(
        message=FakeMessage(out=True, message="/lc_connect"),
        client=client,
        is_private=True,
    )
    result = await handle_in_chat_command(event, ctx)
    assert result == "skip_private"


@pytest.mark.asyncio
async def test_lc_connect_already_connected(inchat_ctx: Any) -> None:
    ctx, sm = inchat_ctx
    client = FakeClient()
    chan = FakeChannel(id=8, title="Existing", megagroup=True)


    marked = _marked(chan.id)
    async with sm() as sess:
        sess.add(
            Cloud(
                chat_id=marked, owner_id=ctx.owner_id, name="X", about="LCLOUD1:fake"
            )
        )
        await sess.commit()
    event = FakeEvent(
        message=FakeMessage(out=True, message="/lc_connect"),
        client=client,
        chat=chan,
    )
    result = await handle_in_chat_command(event, ctx)
    assert result == "already_connected"


@pytest.mark.asyncio
async def test_lc_disconnect_in_cloud_chat(inchat_ctx: Any) -> None:
    ctx, sm = inchat_ctx
    client = FakeClient()
    chan = FakeChannel(id=9, title="ToDrop", megagroup=True)


    marked = _marked(chan.id)
    async with sm() as sess:
        sess.add(
            Cloud(
                chat_id=marked,
                owner_id=ctx.owner_id,
                name="ToDrop",
                about="LCLOUD1:fake",
            )
        )
        await sess.commit()
    event = FakeEvent(
        message=FakeMessage(out=True, message="/lc_disconnect"),
        client=client,
        chat=chan,
    )
    result = await handle_in_chat_command(event, ctx)
    assert result == "disconnected"
    async with sm() as sess:
        assert (await sess.execute(sa.select(Cloud))).scalars().all() == []
    assert any(call[1] == "" for call in client.edit_about_calls)


@pytest.mark.asyncio
async def test_lc_disconnect_in_non_cloud_chat(inchat_ctx: Any) -> None:
    ctx, _ = inchat_ctx
    client = FakeClient()
    chan = FakeChannel(id=10, title="not a cloud", megagroup=True)
    event = FakeEvent(
        message=FakeMessage(out=True, message="/lc_disconnect"),
        client=client,
        chat=chan,
    )
    result = await handle_in_chat_command(event, ctx)
    assert result == "not_a_cloud"


@pytest.mark.asyncio
async def test_lc_random_text_ignored(inchat_ctx: Any) -> None:
    ctx, _ = inchat_ctx
    client = FakeClient()
    chan = FakeChannel(id=11, title="x", megagroup=True)
    event = FakeEvent(
        message=FakeMessage(out=True, message="hello world"),
        client=client,
        chat=chan,
    )
    result = await handle_in_chat_command(event, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_lc_incoming_ignored(inchat_ctx: Any) -> None:
    """Defence-in-depth: only outgoing /lc_* should be handled."""
    ctx, _ = inchat_ctx
    client = FakeClient()
    chan = FakeChannel(id=12, title="x", megagroup=True)
    event = FakeEvent(
        message=FakeMessage(out=False, message="/lc_connect"),
        client=client,
        chat=chan,
    )
    result = await handle_in_chat_command(event, ctx)
    assert result is None


# Silence unused-import warnings for fixture-helper imports
_ = (field,)
