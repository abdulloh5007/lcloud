"""Tests for V2 API key minting/listing/revocation + dual auth (cookie + Bearer)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from lcloud.auth import api_keys as ak
from lcloud.auth.seed import derive_keypair, generate_mnemonic


@pytest.fixture
def app_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    db_file = tmp_path / "lcloud.db"
    monkeypatch.setenv("LC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LC_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setenv("TG_API_ID", "0")
    monkeypatch.setenv("TG_API_HASH", "")
    monkeypatch.setenv("LC_ADMIN_TG_ID", "0")
    monkeypatch.setenv("LC_COOKIE_SECURE", "false")

    from lcloud.api import auth_v2 as auth_v2_mod
    from lcloud.config import get_settings
    from lcloud.db import base as base_mod
    from lcloud.userbot.client import set_userbot_manager

    get_settings.cache_clear()
    base_mod._engine = None
    base_mod._sessionmaker = None
    set_userbot_manager(None)
    auth_v2_mod._v2_rate.reset()

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


def _login(client: TestClient) -> tuple[str, SigningKey, int]:
    """Register/login a fresh user via V2 auth. Returns (pub_hex, sk, user_id)."""
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
    return pub_hex, sk, r2.json()["user_id"]


# -------------------------------------------------------------- mint helpers


def test_mint_key_format() -> None:
    minted = ak.mint_key()
    assert minted.raw.startswith("lck_")
    assert len(minted.raw) > ak.PREFIX_LEN + 8
    assert minted.prefix == minted.raw[: ak.PREFIX_LEN]
    assert minted.hash.startswith("$argon2")


def test_verify_correct_key_passes() -> None:
    minted = ak.mint_key()
    assert ak.verify(minted.raw, minted.hash) is True


def test_verify_wrong_key_fails() -> None:
    a = ak.mint_key()
    b = ak.mint_key()
    assert ak.verify(b.raw, a.hash) is False


def test_verify_garbage_does_not_crash() -> None:
    assert ak.verify("not-a-real-key", "$argon2id$bogus") is False
    assert ak.verify("", "") is False


def test_looks_like_api_key() -> None:
    assert ak.looks_like_api_key("lck_xxxxxxxxxxxx") is True
    assert ak.looks_like_api_key("lck_") is False
    assert ak.looks_like_api_key("not_a_key") is False
    assert ak.looks_like_api_key(None) is False


# -------------------------------------------------------------- API endpoints


def test_mint_endpoint_returns_raw_once_and_persists_meta(
    app_client: TestClient,
) -> None:
    _login(app_client)
    r = app_client.post("/api/v1/keys", json={"label": "ci-bot"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["raw"].startswith("lck_")
    assert body["prefix"] == body["raw"][:12]
    assert body["label"] == "ci-bot"
    assert body["revoked_at"] is None

    # List should NOT contain raw
    r2 = app_client.get("/api/v1/keys")
    assert r2.status_code == 200
    keys = r2.json()
    assert len(keys) == 1
    assert "raw" not in keys[0]
    assert keys[0]["prefix"] == body["prefix"]


def test_listed_keys_isolated_per_user(app_client: TestClient) -> None:
    # User A mints a key
    _login(app_client)
    app_client.post("/api/v1/keys", json={"label": "A"})

    # User B logs in (clears cookie via re-login with new key)
    app_client.cookies.clear()
    _login(app_client)
    r = app_client.get("/api/v1/keys")
    assert r.json() == []  # user B sees no keys


def test_revoke_marks_key_inactive(app_client: TestClient) -> None:
    _login(app_client)
    minted = app_client.post("/api/v1/keys", json={"label": "x"}).json()
    key_id = minted["id"]

    r = app_client.delete(f"/api/v1/keys/{key_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    listed = app_client.get("/api/v1/keys").json()
    assert listed[0]["revoked_at"] is not None


def test_revoke_other_users_key_returns_404(app_client: TestClient) -> None:
    # User A mints
    _login(app_client)
    minted = app_client.post("/api/v1/keys", json={"label": "A"}).json()
    key_id = minted["id"]
    # User B tries to revoke
    app_client.cookies.clear()
    _login(app_client)
    r = app_client.delete(f"/api/v1/keys/{key_id}")
    assert r.status_code == 404


def test_mint_requires_auth(app_client: TestClient) -> None:
    r = app_client.post("/api/v1/keys", json={"label": "x"})
    assert r.status_code == 401


def test_key_limit_enforced(app_client: TestClient) -> None:
    _login(app_client)
    # Mint up to MAX_KEYS_PER_USER
    from lcloud.api.api_keys import MAX_KEYS_PER_USER

    for i in range(MAX_KEYS_PER_USER):
        r = app_client.post("/api/v1/keys", json={"label": f"k{i}"})
        assert r.status_code == 200
    # Next one rejected
    r = app_client.post("/api/v1/keys", json={"label": "boom"})
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "key_limit_reached"


# -------------------------------------------------------------- Bearer auth


def test_bearer_auth_works_for_get_keys(app_client: TestClient) -> None:
    """Mint a key via cookie; then use that key as Bearer to list keys."""
    _login(app_client)
    minted = app_client.post("/api/v1/keys", json={"label": "bot"}).json()
    raw = minted["raw"]

    # Drop cookie, try with Bearer token instead
    app_client.cookies.clear()
    r = app_client.get(
        "/api/v1/keys", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 200, r.text
    listed = r.json()
    assert len(listed) == 1
    assert listed[0]["prefix"] == minted["prefix"]
    # last_used_at should now be set
    assert listed[0]["last_used_at"] is not None


def test_bearer_revoked_key_rejected(app_client: TestClient) -> None:
    _login(app_client)
    minted = app_client.post("/api/v1/keys", json={"label": "bot"}).json()
    key_id = minted["id"]
    raw = minted["raw"]

    # Revoke via cookie session
    app_client.delete(f"/api/v1/keys/{key_id}")

    # Try Bearer with revoked key
    app_client.cookies.clear()
    r = app_client.get(
        "/api/v1/keys", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 401


def test_bearer_garbage_token_401(app_client: TestClient) -> None:
    r = app_client.get(
        "/api/v1/keys",
        headers={"Authorization": "Bearer lck_completelybogus"},
    )
    assert r.status_code == 401


def test_bearer_wrong_scheme_falls_through(app_client: TestClient) -> None:
    r = app_client.get(
        "/api/v1/keys", headers={"Authorization": "Basic foo:bar"}
    )
    assert r.status_code == 401  # no other auth, so 401
