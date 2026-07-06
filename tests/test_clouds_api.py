"""End-to-end tests for the /clouds router with a mocked userbot client."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lcloud.config import Settings
from lcloud.userbot.client import UserbotManager
from tests.test_userbot import FakeTelegramClient


def _bootstrap_isolated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Settings:
    monkeypatch.setenv("LC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LC_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'lcloud.db'}")
    monkeypatch.setenv("LC_SESSION_FILE", str(tmp_path / "session.lcloud"))
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
def authenticated_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, FakeTelegramClient]]:
    """TestClient with an active admin lc_session cookie."""
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
            # Sign in to get the lc_session cookie
            r = client.post("/auth/telegram/start", json={"phone": "+1234567"})
            assert r.status_code == 200
            r = client.post("/auth/telegram/code", json={"code": "12345"})
            assert r.status_code == 200
            assert "lc_session" in client.cookies
            yield client, fake
    finally:
        set_userbot_manager(None)
        _gs.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None


def test_unauthenticated_clouds_listing_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bootstrap_isolated_env(tmp_path, monkeypatch)
    from lcloud.api.auth import get_login_rate_limiter
    from lcloud.config import get_settings
    from lcloud.db import base as base_mod
    from lcloud.main import create_app
    from lcloud.userbot.client import set_userbot_manager

    set_userbot_manager(None)
    get_login_rate_limiter().reset()

    app = create_app()
    try:
        with TestClient(app) as client:
            r = client.get("/clouds")
            assert r.status_code == 401
            assert r.json()["detail"]["reason"] == "no_session"
    finally:
        set_userbot_manager(None)
        get_settings.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None


def test_list_clouds_empty_when_no_rows(
    authenticated_client: tuple[TestClient, FakeTelegramClient],
) -> None:
    client, _ = authenticated_client
    r = client.get("/clouds")
    assert r.status_code == 200
    assert r.json() == []


def test_create_cloud_calls_userbot_and_persists(
    authenticated_client: tuple[TestClient, FakeTelegramClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """We mock create_cloud_chat to avoid hitting Telethon; assert the row
    is persisted with the right marker/chat_id."""
    client, _ = authenticated_client

    captured: dict[str, Any] = {}

    async def fake_create_cloud_chat(
        client_arg: Any, *, name: str, signing_key: Any
    ) -> tuple[int, str, Any]:
        captured["name"] = name
        captured["pubkey"] = bytes(signing_key.verify_key)
        return -1_001_111_111_111, "LCLOUD1:fake", object()

    import lcloud.api.clouds as clouds_mod

    monkeypatch.setattr(clouds_mod, "create_cloud_chat", fake_create_cloud_chat)

    r = client.post("/clouds", json={"name": "Photos"})
    assert r.status_code == 201
    body = r.json()
    assert body["chat_id"] == -1_001_111_111_111
    assert body["name"] == "Photos"
    assert captured["name"] == "Photos"
    assert len(captured["pubkey"]) == 32

    # Listing now returns it
    r = client.get("/clouds")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "Photos"


def test_create_cloud_validates_name(
    authenticated_client: tuple[TestClient, FakeTelegramClient],
) -> None:
    client, _ = authenticated_client
    r = client.post("/clouds", json={"name": ""})
    assert r.status_code == 422
    r = client.post("/clouds", json={"name": "x" * 200})
    assert r.status_code == 422


def test_disconnect_cloud_removes_db_row(
    authenticated_client: tuple[TestClient, FakeTelegramClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = authenticated_client

    async def fake_create(*args: Any, **kwargs: Any) -> tuple[int, str, Any]:
        return -1_002_222_222_222, "LCLOUD1:fake", object()

    async def fake_clear(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_get_entity(self: Any, chat_id: int) -> Any:
        return object()

    import lcloud.api.clouds as clouds_mod

    monkeypatch.setattr(clouds_mod, "create_cloud_chat", fake_create)
    monkeypatch.setattr(clouds_mod, "clear_cloud_marker", fake_clear)
    # Patch FakeTelegramClient.get_entity to be a no-op
    monkeypatch.setattr(FakeTelegramClient, "get_entity", fake_get_entity, raising=False)

    r = client.post("/clouds", json={"name": "Trash"})
    assert r.status_code == 201
    cloud_id = r.json()["id"]

    r = client.delete(f"/clouds/{cloud_id}")
    assert r.status_code == 204

    r = client.get("/clouds")
    assert r.json() == []


def test_create_cloud_when_userbot_not_authorized_returns_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cookie is valid (authed) but the userbot lost auth → 409."""
    settings = _bootstrap_isolated_env(tmp_path, monkeypatch)

    fake = FakeTelegramClient(me_id=42)
    mgr = UserbotManager(settings)
    monkeypatch.setattr(mgr, "_build_client", lambda: fake)  # type: ignore[arg-type]

    from lcloud.api.auth import get_login_rate_limiter
    from lcloud.config import get_settings
    from lcloud.db import base as base_mod
    from lcloud.main import create_app
    from lcloud.userbot.client import set_userbot_manager

    set_userbot_manager(mgr)
    get_login_rate_limiter().reset()

    app = create_app()
    try:
        with TestClient(app) as client:
            client.post("/auth/telegram/start", json={"phone": "+1234567"})
            client.post("/auth/telegram/code", json={"code": "x"})
            assert "lc_session" in client.cookies

            # Force the userbot back to "not authorized"
            fake._authorized = False

            r = client.post("/clouds", json={"name": "X"})
            assert r.status_code == 409
            assert r.json()["detail"]["reason"] == "userbot_not_authorized"
    finally:
        set_userbot_manager(None)
        get_settings.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None
