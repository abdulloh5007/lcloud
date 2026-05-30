"""Tests for the magic-link admin login: token issuance + GET /admin endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from lcloud.auth.jwt_utils import (
    decode_admin_token,
    ensure_jwt_secret,
    issue_admin_token,
    issue_magic_token,
)
from lcloud.config import Settings
from lcloud.userbot.client import UserbotManager
from tests.test_userbot import FakeTelegramClient

# ---------------------------------------------------------------------- units


def test_magic_token_distinct_kind_from_session(tmp_path: Path) -> None:
    s = Settings(_env_file=None, lc_data_dir=tmp_path)
    ensure_jwt_secret(s)
    magic = issue_magic_token(owner_id=1, auth_epoch=1, settings=s)
    session = issue_admin_token(owner_id=1, auth_epoch=1, settings=s)

    p_magic = decode_admin_token(magic, settings=s)
    p_session = decode_admin_token(session, settings=s)

    assert p_magic["kind"] == "magic"
    assert p_session["kind"] == "session"
    assert p_magic["jti"] != p_session["jti"]


def test_magic_token_short_ttl(tmp_path: Path) -> None:
    s = Settings(
        _env_file=None,
        lc_data_dir=tmp_path,
        lc_magic_link_ttl_seconds=900,
        lc_session_ttl_seconds=86400,
    )
    ensure_jwt_secret(s)
    magic = issue_magic_token(owner_id=1, auth_epoch=1, settings=s)
    session = issue_admin_token(owner_id=1, auth_epoch=1, settings=s)
    pm = decode_admin_token(magic, settings=s)
    ps = decode_admin_token(session, settings=s)
    assert pm["exp"] - pm["iat"] == 900
    assert ps["exp"] - ps["iat"] == 86400


def test_magic_token_expired(tmp_path: Path) -> None:
    s = Settings(_env_file=None, lc_data_dir=tmp_path)
    ensure_jwt_secret(s)
    long_ago = int(__import__("time").time()) - s.lc_magic_link_ttl_seconds - 60
    token = issue_magic_token(owner_id=1, auth_epoch=1, settings=s, now=long_ago)
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_admin_token(token, settings=s)


# ---------------------------------------------------------------------- e2e


def _bootstrap_isolated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Settings:
    monkeypatch.setenv("LC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LC_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'lcloud.db'}")
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "testhash")
    monkeypatch.setenv("LC_ADMIN_TG_ID", "42")
    monkeypatch.setenv("LC_COOKIE_SECURE", "false")

    from lcloud.config import get_settings
    from lcloud.db import base as base_mod

    get_settings.cache_clear()
    base_mod._engine = None
    base_mod._sessionmaker = None
    return get_settings()


@pytest.fixture
def authed_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Settings]]:
    settings = _bootstrap_isolated_env(tmp_path, monkeypatch)

    fake = FakeTelegramClient(me_id=42)
    mgr = UserbotManager(settings)
    monkeypatch.setattr(mgr, "_build_client", lambda: fake)  # type: ignore[arg-type]

    from lcloud.api.auth import get_login_rate_limiter
    from lcloud.userbot.client import set_userbot_manager

    set_userbot_manager(mgr)
    get_login_rate_limiter().reset()

    from lcloud.config import get_settings as _gs
    from lcloud.db import base as base_mod
    from lcloud.main import create_app

    app = create_app()
    try:
        with TestClient(app) as client:
            # one login to seed admin owner row + auth_state
            client.post("/auth/telegram/start", json={"phone": "+1234567"})
            client.post("/auth/telegram/code", json={"code": "x"})
            yield client, settings
    finally:
        set_userbot_manager(None)
        _gs.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None


def _issue_magic_for_admin(client: TestClient, settings: Settings) -> str:
    """Helper: get a magic token for the admin owner currently in the DB."""
    import sqlite3

    db_path = settings.data_dir / "lcloud.db"
    c = sqlite3.connect(str(db_path))
    owner_id = c.execute(
        "SELECT id FROM owners WHERE role='admin' LIMIT 1"
    ).fetchone()[0]
    epoch = c.execute(
        "SELECT epoch FROM auth_state WHERE owner_id=?", (owner_id,)
    ).fetchone()[0]
    c.close()
    return issue_magic_token(
        owner_id=owner_id, auth_epoch=epoch, settings=settings
    )


def test_magic_login_sets_cookie_and_redirects(
    authed_app: tuple[TestClient, Settings],
) -> None:
    client, settings = authed_app
    # New client without cookie
    client.cookies.clear()
    token = _issue_magic_for_admin(client, settings)

    r = client.get(f"/admin?token={token}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    assert "lc_session" in client.cookies
    # Now /clouds works (auth via the new cookie)
    r = client.get("/clouds")
    assert r.status_code == 200


def test_magic_link_replay_rejected(
    authed_app: tuple[TestClient, Settings],
) -> None:
    client, settings = authed_app
    client.cookies.clear()
    token = _issue_magic_for_admin(client, settings)

    r = client.get(f"/admin?token={token}", follow_redirects=False)
    assert r.status_code == 302

    client.cookies.clear()
    r = client.get(f"/admin?token={token}", follow_redirects=False)
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "replay"


def test_magic_link_session_token_rejected(
    authed_app: tuple[TestClient, Settings],
) -> None:
    """A session-cookie JWT must NOT be accepted as a magic-link."""
    client, settings = authed_app
    client.cookies.clear()
    # Use the session-style token
    fake_session_token = issue_admin_token(
        owner_id=1, auth_epoch=1, settings=settings
    )
    r = client.get(
        f"/admin?token={fake_session_token}", follow_redirects=False
    )
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "wrong_kind"


def test_magic_link_garbage_token_rejected(
    authed_app: tuple[TestClient, Settings],
) -> None:
    client, _ = authed_app
    client.cookies.clear()
    # 50+ char "JWT-shaped" garbage — passes Query length validation, fails decode
    fake = "header." + ("x" * 60) + ".sig"
    r = client.get(f"/admin?token={fake}", follow_redirects=False)
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "invalid_token"


def test_magic_link_revoked_epoch_rejected(
    authed_app: tuple[TestClient, Settings], tmp_path: Path
) -> None:
    """If `auth_state.epoch` is bumped (e.g., /revoke), an outstanding magic
    link with the OLD epoch must be rejected."""
    client, settings = authed_app
    client.cookies.clear()
    token = _issue_magic_for_admin(client, settings)

    # Bump epoch directly in the DB
    import sqlite3

    c = sqlite3.connect(str(settings.data_dir / "lcloud.db"))
    c.execute("UPDATE auth_state SET epoch = epoch + 1")
    c.commit()
    c.close()

    r = client.get(f"/admin?token={token}", follow_redirects=False)
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "epoch_mismatch"


# ---------------------------------------------------------------------- saved


def test_saved_admin_command_returns_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/admin` in Saved Messages replies with a URL containing a token."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from lcloud.db.bootstrap import run_migrations_sync
    from lcloud.db.models import AuthState, Owner
    from lcloud.userbot.commands import (
        CommandContext,
        handle_saved_messages_command,
    )

    s = Settings(
        _env_file=None,
        lc_data_dir=tmp_path,
        lc_db_url=f"sqlite+aiosqlite:///{tmp_path / 'lcloud.db'}",
        lc_public_base_url="https://example.test",
    )
    run_migrations_sync(s)

    import asyncio

    async def go() -> str | None:
        eng = create_async_engine(s.lc_db_url, future=True)
        sm: async_sessionmaker[Any] = async_sessionmaker(eng, expire_on_commit=False)
        async with sm() as sess:
            sess.add(Owner(pubkey=b"\x01" * 32, label="admin", role="admin"))
            await sess.commit()
            owner = (
                await sess.execute(select(Owner).where(Owner.role == "admin"))
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

        from dataclasses import dataclass

        @dataclass
        class FakeMsg:
            out: bool
            message: str

        class FakeClient:
            def __init__(self) -> None:
                self.replies: list[str] = []

            async def send_message(self, peer: str, text: str, **_: Any) -> None:
                if peer == "me":
                    self.replies.append(text)

        @dataclass
        class FakeEvent:
            message: FakeMsg
            client: FakeClient

        evt = FakeEvent(
            message=FakeMsg(out=True, message="/admin"), client=FakeClient()
        )
        reply = await handle_saved_messages_command(evt, ctx)
        await eng.dispose()
        return reply

    reply = asyncio.run(go())
    assert reply is not None
    assert "https://example.test/admin?token=" in reply
    assert "valid" in reply.lower()
