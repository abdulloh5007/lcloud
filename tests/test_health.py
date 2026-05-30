"""Smoke tests for HTTP endpoints. Lifespan is exercised against a tmp DB."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lcloud import __version__


@pytest.fixture
def isolated_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Build a fresh app + tmp data dir + tmp DB for lifespan to operate on.

    Uses env vars (so any module that imported `get_settings` directly still
    sees the new values after `cache_clear`).
    """
    db_file = tmp_path / "lcloud.db"
    monkeypatch.setenv("LC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LC_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    # Force userbot into degraded mode so lifespan doesn't open a real Telethon
    # connection during smoke tests.
    monkeypatch.setenv("TG_API_ID", "0")
    monkeypatch.setenv("TG_API_HASH", "")
    monkeypatch.setenv("LC_ADMIN_TG_ID", "0")
    monkeypatch.setenv("LC_COOKIE_SECURE", "false")

    from lcloud.config import get_settings
    from lcloud.db import base as base_mod
    from lcloud.userbot.client import set_userbot_manager

    get_settings.cache_clear()
    base_mod._engine = None
    base_mod._sessionmaker = None
    set_userbot_manager(None)

    from lcloud.main import create_app

    app = create_app()
    try:
        with TestClient(app) as client:
            yield client
    finally:
        get_settings.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None
        set_userbot_manager(None)


def test_health_endpoint(isolated_app: TestClient) -> None:
    resp = isolated_app.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_root_endpoint(isolated_app: TestClient) -> None:
    """`GET /` either serves the built SPA index.html (production) or a
    JSON fallback when the frontend isn't built. Accept both shapes."""
    resp = isolated_app.get("/")
    assert resp.status_code == 200
    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        body = resp.json()
        assert body["name"] == "LCloud"
        assert body["version"] == __version__
    else:
        # SPA index.html — must contain our root mount + bundled script
        assert "<div id=\"root\"" in resp.text
        assert "/assets/" in resp.text or "<script" in resp.text


def test_lifespan_creates_keypair_and_db(
    isolated_app: TestClient, tmp_path: Path
) -> None:
    """After app boots, admin keys + DB schema must be on disk."""
    assert (tmp_path / "keys" / "admin.key").exists()
    assert (tmp_path / "keys" / "admin.pub").exists()
    assert (tmp_path / "lcloud.db").exists()
