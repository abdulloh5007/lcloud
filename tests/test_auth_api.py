"""Integration tests for the /auth router using a mocked UserbotManager."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lcloud.config import Settings
from lcloud.userbot.client import (
    LoginFlowState,
    UserbotManager,
)
from tests.test_userbot import FakeTelegramClient


def _bootstrap_isolated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Settings:
    monkeypatch.setenv("LC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LC_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'lcloud.db'}")
    # Force degraded Telethon: lifespan won't try to connect; we'll inject a manager
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
def authed_admin_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, FakeTelegramClient, UserbotManager]]:
    """App where the userbot is wired to a fake TelegramClient (admin id=42)."""
    settings = _bootstrap_isolated_env(tmp_path, monkeypatch)

    fakes: list[FakeTelegramClient] = []

    def factory() -> FakeTelegramClient:
        fake = FakeTelegramClient(me_id=42)
        fakes.append(fake)
        return fake

    mgr = UserbotManager(settings)
    monkeypatch.setattr(mgr, "_build_client", factory)  # type: ignore[arg-type]

    from lcloud.userbot.client import set_userbot_manager

    set_userbot_manager(mgr)

    # Reset the in-process rate limiter between tests
    from lcloud.api.auth import get_login_rate_limiter

    get_login_rate_limiter().reset()

    from lcloud.main import create_app

    app = create_app()
    try:
        with TestClient(app) as client:
            yield client, fakes[0], mgr
    finally:
        from lcloud.config import get_settings as _gs
        from lcloud.db import base as base_mod

        set_userbot_manager(None)
        _gs.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None


@pytest.fixture
def wrong_account_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, list[FakeTelegramClient], UserbotManager]]:
    """App where the fake TelegramClient reports a non-admin user_id."""
    settings = _bootstrap_isolated_env(tmp_path, monkeypatch)

    fakes: list[FakeTelegramClient] = []

    def factory() -> FakeTelegramClient:
        fake = FakeTelegramClient(me_id=999)  # NOT admin
        fakes.append(fake)
        return fake

    mgr = UserbotManager(settings)
    monkeypatch.setattr(mgr, "_build_client", factory)  # type: ignore[arg-type]

    from lcloud.userbot.client import set_userbot_manager

    set_userbot_manager(mgr)

    from lcloud.api.auth import get_login_rate_limiter

    get_login_rate_limiter().reset()

    from lcloud.main import create_app

    app = create_app()
    try:
        with TestClient(app) as client:
            yield client, fakes, mgr
    finally:
        from lcloud.config import get_settings as _gs
        from lcloud.db import base as base_mod

        set_userbot_manager(None)
        _gs.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None


# ------------------------------------------------------------------------ tests


def test_auth_state_initial_unauthorized(
    authed_admin_app: tuple[TestClient, FakeTelegramClient, UserbotManager],
) -> None:
    client, _, _ = authed_admin_app
    resp = client.get("/auth/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorized"] is False
    assert body["state"] == LoginFlowState.NO_SESSION.value
    assert body["userbot_started"] is True
    assert body["me"] is None


def test_full_login_flow_no_2fa(
    authed_admin_app: tuple[TestClient, FakeTelegramClient, UserbotManager],
) -> None:
    client, _, _ = authed_admin_app

    r = client.post("/auth/telegram/start", json={"phone": "+1234567"})
    assert r.status_code == 200
    assert r.json()["state"] == LoginFlowState.CODE_SENT.value

    r = client.get("/auth/state")
    assert r.json()["state"] == LoginFlowState.CODE_SENT.value

    r = client.post("/auth/telegram/code", json={"code": "12345"})
    assert r.status_code == 200
    body = r.json()
    assert body["authorized"] is True
    assert body["me"]["id"] == 42
    assert "lc_session" in r.cookies

    # Now /auth/state reports authorized
    r = client.get("/auth/state")
    body = r.json()
    assert body["authorized"] is True
    assert body["me"]["id"] == 42
    assert body["state"] == LoginFlowState.AUTHORIZED.value


def test_login_flow_2fa_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _bootstrap_isolated_env(tmp_path, monkeypatch)
    fake = FakeTelegramClient(me_id=42, require_2fa=True)
    mgr = UserbotManager(settings)
    monkeypatch.setattr(mgr, "_build_client", lambda: fake)  # type: ignore[arg-type]

    from lcloud.api.auth import get_login_rate_limiter
    from lcloud.userbot.client import set_userbot_manager

    set_userbot_manager(mgr)
    get_login_rate_limiter().reset()

    from lcloud.config import get_settings
    from lcloud.db import base as base_mod
    from lcloud.main import create_app

    try:
        app = create_app()
        with TestClient(app) as client:
            client.post("/auth/telegram/start", json={"phone": "+1234567"})
            r = client.post("/auth/telegram/code", json={"code": "x"})
            assert r.status_code == 200
            assert r.json() == {
                "need_password": True,
                "state": LoginFlowState.PWD_NEEDED.value,
            }

            r = client.post(
                "/auth/telegram/password", json={"password": "hunter2"}
            )
            assert r.status_code == 200
            assert r.json()["authorized"] is True
            assert "lc_session" in r.cookies
    finally:
        set_userbot_manager(None)
        get_settings.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None


def test_wrong_account_returns_403_and_archives(
    wrong_account_app: tuple[TestClient, list[FakeTelegramClient], UserbotManager],
    tmp_path: Path,
) -> None:
    client, _, _ = wrong_account_app

    client.post("/auth/telegram/start", json={"phone": "+1234567"})
    r = client.post("/auth/telegram/code", json={"code": "x"})
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["reason"] == "wrong_account"
    assert detail["got"] == 999
    assert detail["expected"] == 42
    assert "lc_session" not in r.cookies

    # archive sidecar must exist
    sidecars = list(tmp_path.glob("session.rejected.*.json"))
    assert len(sidecars) == 1


def test_logout_clears_cookie(
    authed_admin_app: tuple[TestClient, FakeTelegramClient, UserbotManager],
) -> None:
    client, _, _ = authed_admin_app

    # Acquire a session first
    client.post("/auth/telegram/start", json={"phone": "+1234567"})
    client.post("/auth/telegram/code", json={"code": "x"})
    assert "lc_session" in client.cookies

    r = client.post("/auth/logout")
    assert r.status_code == 200
    # TestClient (httpx CookieJar) honours Max-Age=0 / Set-Cookie clearing
    assert "lc_session" not in client.cookies


def test_cancel_flow(
    authed_admin_app: tuple[TestClient, FakeTelegramClient, UserbotManager],
) -> None:
    client, _, _ = authed_admin_app

    client.post("/auth/telegram/start", json={"phone": "+1234567"})
    r = client.get("/auth/state")
    assert r.json()["state"] == LoginFlowState.CODE_SENT.value

    r = client.post("/auth/telegram/cancel")
    assert r.status_code == 200

    r = client.get("/auth/state")
    assert r.json()["state"] == LoginFlowState.NO_SESSION.value


def test_double_code_without_flow_returns_409(
    authed_admin_app: tuple[TestClient, FakeTelegramClient, UserbotManager],
) -> None:
    client, _, _ = authed_admin_app
    r = client.post("/auth/telegram/code", json={"code": "x"})
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "no_active_flow"


def test_rate_limiter_blocks_after_capacity(
    authed_admin_app: tuple[TestClient, FakeTelegramClient, UserbotManager],
) -> None:
    client, _, _ = authed_admin_app

    # First start consumes a token
    client.post("/auth/telegram/start", json={"phone": "+1234567"})
    # Cancel so we can issue more starts
    for _ in range(4):
        client.post("/auth/telegram/cancel")
        client.post("/auth/telegram/start", json={"phone": "+1234567"})

    # 6th request must be rate-limited
    client.post("/auth/telegram/cancel")
    r = client.post("/auth/telegram/start", json={"phone": "+1234567"})
    assert r.status_code == 429
    assert r.json()["detail"]["reason"] == "rate_limited"
