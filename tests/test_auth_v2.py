"""Tests for V2 auth: BIP39 / Ed25519 challenge-response login flow."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from lcloud.auth.seed import derive_keypair, generate_mnemonic


@pytest.fixture
def app_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Fresh app + tmp DB; lifespan runs migrations."""
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
    auth_v2_mod._v2_rate.reset()  # reset per-test rate limiter

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


def _new_user_keypair() -> tuple[str, SigningKey]:
    """Generate fresh BIP39 → Ed25519, return (pubkey_hex, signing_key)."""
    mnemonic = generate_mnemonic(12)
    ident = derive_keypair(mnemonic)
    sk = SigningKey(ident.privkey_seed)
    return ident.pubkey.hex(), sk


def _full_login(client: TestClient, sk: SigningKey, pub_hex: str) -> dict:
    """Helper: do challenge → sign nonce → verify → return verify response JSON."""
    r = client.post("/auth/v2/challenge", json={"pubkey": pub_hex})
    assert r.status_code == 200, r.text
    body = r.json()
    nonce_bytes = bytes.fromhex(body["nonce"])
    sig = sk.sign(nonce_bytes).signature.hex()
    r2 = client.post(
        "/auth/v2/verify",
        json={"challenge_jwt": body["challenge_jwt"], "signature": sig},
    )
    assert r2.status_code == 200, r2.text
    return r2.json()


# -------------------------------------------------------------- core flow


def test_challenge_returns_jwt_and_nonce(app_client: TestClient) -> None:
    pub_hex, _ = _new_user_keypair()
    r = app_client.post("/auth/v2/challenge", json={"pubkey": pub_hex})
    assert r.status_code == 200
    body = r.json()
    assert "challenge_jwt" in body
    assert len(body["nonce"]) == 64  # 32 bytes hex
    assert body["expires_in"] > 0


def test_verify_registers_new_user_and_sets_cookie(
    app_client: TestClient,
) -> None:
    pub_hex, sk = _new_user_keypair()
    body = _full_login(app_client, sk, pub_hex)
    assert body["registered"] is True
    assert body["role"] == "user"
    assert body["user_id"] >= 1
    # Cookie set
    assert "lc_user_session" in app_client.cookies


def test_verify_second_login_does_not_re_register(
    app_client: TestClient,
) -> None:
    pub_hex, sk = _new_user_keypair()
    first = _full_login(app_client, sk, pub_hex)
    second = _full_login(app_client, sk, pub_hex)
    assert first["user_id"] == second["user_id"]
    assert first["registered"] is True
    assert second["registered"] is False


def test_me_returns_user_info_when_logged_in(app_client: TestClient) -> None:
    pub_hex, sk = _new_user_keypair()
    _full_login(app_client, sk, pub_hex)
    r = app_client.get("/auth/v2/me")
    assert r.status_code == 200
    body = r.json()
    assert body["pubkey"] == pub_hex
    assert body["role"] == "user"
    assert body["storage_used_bytes"] == 0
    assert body["storage_quota_bytes"] >= 1024**3  # at least 1 GiB


def test_me_401_without_cookie(app_client: TestClient) -> None:
    r = app_client.get("/auth/v2/me")
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "no_session"


def test_logout_clears_cookie(app_client: TestClient) -> None:
    pub_hex, sk = _new_user_keypair()
    _full_login(app_client, sk, pub_hex)
    r = app_client.post("/auth/v2/logout")
    assert r.status_code == 200
    # cookie deleted by the response
    r2 = app_client.get("/auth/v2/me")
    assert r2.status_code == 401


# -------------------------------------------------------------- security


def test_verify_wrong_signature_rejected(app_client: TestClient) -> None:
    pub_hex, _ = _new_user_keypair()
    r = app_client.post("/auth/v2/challenge", json={"pubkey": pub_hex})
    body = r.json()

    # Use a DIFFERENT signing key to sign the nonce — must fail
    _, other_sk = _new_user_keypair()
    nonce_bytes = bytes.fromhex(body["nonce"])
    bad_sig = other_sk.sign(nonce_bytes).signature.hex()

    r2 = app_client.post(
        "/auth/v2/verify",
        json={"challenge_jwt": body["challenge_jwt"], "signature": bad_sig},
    )
    assert r2.status_code == 401
    assert r2.json()["detail"]["reason"] == "bad_signature"


def test_verify_replay_blocked(app_client: TestClient) -> None:
    pub_hex, sk = _new_user_keypair()
    r = app_client.post("/auth/v2/challenge", json={"pubkey": pub_hex})
    body = r.json()
    nonce_bytes = bytes.fromhex(body["nonce"])
    sig = sk.sign(nonce_bytes).signature.hex()

    # First verify succeeds
    r1 = app_client.post(
        "/auth/v2/verify",
        json={"challenge_jwt": body["challenge_jwt"], "signature": sig},
    )
    assert r1.status_code == 200
    # Replay must fail
    r2 = app_client.post(
        "/auth/v2/verify",
        json={"challenge_jwt": body["challenge_jwt"], "signature": sig},
    )
    assert r2.status_code == 401
    assert r2.json()["detail"]["reason"] == "challenge_replay"


def test_challenge_input_validation(app_client: TestClient) -> None:
    # Wrong pubkey length
    r = app_client.post("/auth/v2/challenge", json={"pubkey": "abcd"})
    assert r.status_code == 422
    # Non-hex
    r = app_client.post("/auth/v2/challenge", json={"pubkey": "z" * 64})
    assert r.status_code == 422


def test_verify_input_validation(app_client: TestClient) -> None:
    # Missing fields
    r = app_client.post("/auth/v2/verify", json={"challenge_jwt": "x"})
    assert r.status_code == 422
    # Wrong sig length
    r = app_client.post(
        "/auth/v2/verify",
        json={"challenge_jwt": "x" * 50, "signature": "abcd"},
    )
    assert r.status_code == 422


def test_garbage_jwt_rejected(app_client: TestClient) -> None:
    _, sk = _new_user_keypair()
    sig = sk.sign(b"\x00" * 32).signature.hex()
    # Use 50+ chars to clear pydantic min_length, expect runtime JWT decode fail
    r = app_client.post(
        "/auth/v2/verify",
        json={"challenge_jwt": "x" * 60, "signature": sig},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "invalid_challenge"
