"""Tests for /api/v1/files/{id}/shares and /share/{token}."""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from lcloud.auth.seed import derive_keypair, generate_mnemonic
from lcloud.userbot.files import UploadResult


@pytest.fixture
def app_with_userbot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    db_file = tmp_path / "lcloud.db"
    monkeypatch.setenv("LC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LC_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "x")
    monkeypatch.setenv("LC_ADMIN_TG_ID", "42")
    monkeypatch.setenv("LC_COOKIE_SECURE", "false")
    monkeypatch.setenv("LC_MAX_FILE_BYTES", "10000000")

    from lcloud.api import auth_v2 as auth_v2_mod
    from lcloud.api import payments as payments_mod
    from lcloud.cache import cache as global_cache
    from lcloud.config import get_settings
    from lcloud.db import base as base_mod
    from lcloud.userbot.client import UserbotManager, set_userbot_manager

    get_settings.cache_clear()
    base_mod._engine = None
    base_mod._sessionmaker = None
    auth_v2_mod._v2_rate.reset()
    payments_mod._pay_rate.reset()
    asyncio.run(global_cache.clear())

    settings = get_settings()
    from tests.test_userbot import FakeTelegramClient

    fake_tg = FakeTelegramClient(me_id=42)
    mgr = UserbotManager(settings)
    monkeypatch.setattr(mgr, "_build_client", lambda: fake_tg)
    set_userbot_manager(mgr)

    next_chat_id = [-1_001_555_000_000]

    async def fake_create(client: Any, *, name: str, signing_key: Any) -> tuple[int, str, Any]:
        next_chat_id[0] += 1
        return next_chat_id[0], "LCLOUD1:fake", object()

    next_msg = [1000]

    async def fake_upload(
        client, *, chat_id, file_path, original_name, sha256_digest, signing_key
    ) -> UploadResult:
        next_msg[0] += 1
        return UploadResult(
            message_id=next_msg[0],
            caption="LC1:{}",
            uploaded_at_unix=1700000000,
            signature=b"\x00" * 64,
        )

    async def fake_delete(client, *, chat_id, message_id) -> None:
        return None

    async def fake_iter(client, *, chat_id, message_id):
        yield b"hello world contents " * 100  # ~2 KB

    import lcloud.api.shares as shares_mod
    import lcloud.api.v2_clouds as v2_clouds_mod
    import lcloud.api.v2_files as v2_files_mod

    monkeypatch.setattr(v2_clouds_mod, "create_cloud_chat", fake_create)
    monkeypatch.setattr(v2_files_mod, "upload_file_to_cloud", fake_upload)
    monkeypatch.setattr(v2_files_mod, "delete_file_message", fake_delete)
    monkeypatch.setattr(shares_mod, "iter_download_file", fake_iter)

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
        auth_v2_mod._v2_rate.reset()
        payments_mod._pay_rate.reset()
        asyncio.run(global_cache.clear())


def _login_admin_telegram(client: TestClient) -> None:
    from lcloud.api.auth import get_login_rate_limiter

    get_login_rate_limiter().reset()
    r = client.post("/auth/telegram/start", json={"phone": "+1234567"})
    assert r.status_code == 200
    r = client.post("/auth/telegram/code", json={"code": "12345"})
    assert r.status_code == 200
    client.cookies.clear()


def _login_v2(client: TestClient) -> tuple[int, SigningKey]:
    mnemonic = generate_mnemonic(12)
    ident = derive_keypair(mnemonic)
    sk = SigningKey(ident.privkey_seed)
    pub_hex = ident.pubkey.hex()
    r = client.post("/auth/v2/challenge", json={"pubkey": pub_hex})
    body = r.json()
    nonce = bytes.fromhex(body["nonce"])
    sig = sk.sign(nonce).signature.hex()
    r2 = client.post(
        "/auth/v2/verify",
        json={"challenge_jwt": body["challenge_jwt"], "signature": sig},
    )
    return r2.json()["user_id"], sk


def _create_cloud(client: TestClient) -> int:
    return int(client.post("/api/v1/clouds", json={"name": "C"}).json()["id"])


def _upload(client: TestClient, cloud_id: int, name: str = "x.bin") -> dict:
    r = client.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": (name, io.BytesIO(b"hello world"), "text/plain")},
    )
    assert r.status_code == 201
    return r.json()


# ============================================================ shares


def test_create_share_returns_url(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)
    file = _upload(app_with_userbot, cloud_id)

    r = app_with_userbot.post(f"/api/v1/files/{file['id']}/shares", json={})
    assert r.status_code == 201
    body = r.json()
    assert body["token"]
    assert body["url"].endswith(f"/share/{body['token']}")
    assert body["active"] is True
    assert body["max_downloads"] is None
    assert body["expires_at"] is None


def test_share_with_expiration_and_limit(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)
    file = _upload(app_with_userbot, cloud_id)

    r = app_with_userbot.post(
        f"/api/v1/files/{file['id']}/shares",
        json={"expires_in_seconds": 3600, "max_downloads": 3},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["max_downloads"] == 3
    assert body["expires_at"] is not None


def test_share_other_users_file_404(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    # User A
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)
    file = _upload(app_with_userbot, cloud_id)
    # User B tries to share user A's file
    app_with_userbot.cookies.clear()
    _login_v2(app_with_userbot)
    r = app_with_userbot.post(f"/api/v1/files/{file['id']}/shares", json={})
    assert r.status_code == 404


def test_public_download_works(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)
    file = _upload(app_with_userbot, cloud_id)
    share = app_with_userbot.post(
        f"/api/v1/files/{file['id']}/shares", json={}
    ).json()

    # Anonymous
    app_with_userbot.cookies.clear()
    r = app_with_userbot.get(f"/share/{share['token']}")
    assert r.status_code == 200, r.text
    assert b"hello world contents" in r.content


def test_revoked_share_returns_404(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)
    file = _upload(app_with_userbot, cloud_id)
    share = app_with_userbot.post(
        f"/api/v1/files/{file['id']}/shares", json={}
    ).json()
    rev = app_with_userbot.delete(f"/api/v1/shares/{share['id']}")
    assert rev.status_code == 204

    app_with_userbot.cookies.clear()
    r = app_with_userbot.get(f"/share/{share['token']}")
    assert r.status_code == 404


def test_share_max_downloads_enforced(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)
    file = _upload(app_with_userbot, cloud_id)
    share = app_with_userbot.post(
        f"/api/v1/files/{file['id']}/shares",
        json={"max_downloads": 1},
    ).json()

    app_with_userbot.cookies.clear()
    r1 = app_with_userbot.get(f"/share/{share['token']}")
    assert r1.status_code == 200
    r2 = app_with_userbot.get(f"/share/{share['token']}")
    assert r2.status_code == 404


# ============================================================ versioning


def test_re_upload_marks_previous_as_superseded(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)
    f1 = _upload(app_with_userbot, cloud_id, "report.pdf")
    f2 = _upload(app_with_userbot, cloud_id, "report.pdf")  # same name

    assert f1["id"] != f2["id"]

    # File listing only shows the new one
    list_r = app_with_userbot.get(f"/api/v1/clouds/{cloud_id}/files")
    items = list_r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == f2["id"]

    # Versions endpoint returns both
    vers_r = app_with_userbot.get(f"/api/v1/files/{f2['id']}/versions")
    assert vers_r.status_code == 200
    versions = vers_r.json()
    ids = {v["id"] for v in versions}
    assert {f1["id"], f2["id"]}.issubset(ids)


def test_versions_cross_user_404(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)
    f = _upload(app_with_userbot, cloud_id, "doc.txt")
    # Other user
    app_with_userbot.cookies.clear()
    _login_v2(app_with_userbot)
    r = app_with_userbot.get(f"/api/v1/files/{f['id']}/versions")
    assert r.status_code == 404


# ============================================================ /metrics


def test_metrics_endpoint_responds(app_with_userbot: TestClient) -> None:
    r = app_with_userbot.get("/metrics")
    assert r.status_code == 200
    text = r.text
    # Standard prom-fastapi-instrumentator metrics
    assert "http_requests_total" in text
