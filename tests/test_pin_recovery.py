"""Tests for /auth/v2/pin/setup + /auth/v2/pin/recover."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from lcloud.auth import pin_recovery as pin_module
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
    from lcloud.api import payments as payments_mod
    from lcloud.api import pin_recovery as pin_endpoints
    from lcloud.cache import cache as global_cache
    from lcloud.config import get_settings
    from lcloud.db import base as base_mod
    from lcloud.userbot.client import set_userbot_manager

    get_settings.cache_clear()
    base_mod._engine = None
    base_mod._sessionmaker = None
    set_userbot_manager(None)
    auth_v2_mod._v2_rate.reset()
    payments_mod._pay_rate.reset()
    pin_endpoints._recover_rate.reset()
    asyncio.run(global_cache.clear())

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
        pin_endpoints._recover_rate.reset()
        asyncio.run(global_cache.clear())


def _login_with_seed(client: TestClient, mnemonic: str) -> tuple[int, str]:
    """Login via V2 challenge-response. Returns (user_id, pubkey_hex)."""
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
    assert r2.status_code == 200, r2.text
    return r2.json()["user_id"], pub_hex


async def _set_contact_handle(user_id: int, handle: str) -> None:
    """Mark user.contact_handle (otherwise /recover can't find them)."""
    from lcloud.db.base import get_sessionmaker
    from lcloud.db.models import User

    sm = get_sessionmaker()
    async with sm() as sess:
        await sess.execute(
            sa.update(User).where(User.id == user_id).values(contact_handle=handle)
        )
        await sess.commit()


# ============================================================ helper unit


def test_pin_validator() -> None:
    assert pin_module.is_valid_pin("1234") is True
    assert pin_module.is_valid_pin("0000") is True
    assert pin_module.is_valid_pin("123") is False
    assert pin_module.is_valid_pin("12345") is False
    assert pin_module.is_valid_pin("abcd") is False
    assert pin_module.is_valid_pin("12 4") is False
    assert pin_module.is_valid_pin("") is False


def test_encrypt_decrypt_roundtrip() -> None:
    pin = "1234"
    mnemonic = "abandon ability " * 6 + "art"
    salt, ct = pin_module.encrypt_seed(pin, mnemonic.strip())
    assert len(salt) >= 16
    assert ct  # not empty
    assert ct != mnemonic.encode()  # actually encrypted

    # Right pin → decrypts
    plain = pin_module.decrypt_seed("1234", salt, ct)
    assert plain == mnemonic.strip()

    # Wrong pin → returns None (MAC fails)
    plain_wrong = pin_module.decrypt_seed("9999", salt, ct)
    assert plain_wrong is None


def test_hash_and_verify() -> None:
    h = pin_module.hash_pin("4321")
    assert h.startswith("$argon2")
    assert pin_module.verify_pin("4321", h) is True
    assert pin_module.verify_pin("4322", h) is False


def test_decrypt_handles_garbage() -> None:
    salt = b"\x00" * 16
    assert pin_module.decrypt_seed("1234", salt, b"garbage") is None


# ============================================================ /setup


def test_setup_requires_session(app_client: TestClient) -> None:
    r = app_client.post(
        "/auth/v2/pin/setup",
        json={"pin": "1234", "mnemonic": "abandon ability " * 6 + "art"},
    )
    assert r.status_code == 401


def test_setup_rejects_bad_pin(app_client: TestClient) -> None:
    mnemonic = generate_mnemonic(12)
    _login_with_seed(app_client, mnemonic)
    # Three letters
    r = app_client.post(
        "/auth/v2/pin/setup", json={"pin": "abcd", "mnemonic": mnemonic}
    )
    assert r.status_code == 400


def test_setup_rejects_bad_mnemonic(app_client: TestClient) -> None:
    mnemonic = generate_mnemonic(12)
    _login_with_seed(app_client, mnemonic)
    r = app_client.post(
        "/auth/v2/pin/setup",
        json={"pin": "1234", "mnemonic": "not a real mnemonic phrase at all"},
    )
    assert r.status_code == 400


def test_setup_rejects_other_users_mnemonic(app_client: TestClient) -> None:
    """User must back up their OWN seed phrase, not someone else's."""
    my_mnemonic = generate_mnemonic(12)
    _login_with_seed(app_client, my_mnemonic)

    different_mnemonic = generate_mnemonic(12)
    r = app_client.post(
        "/auth/v2/pin/setup",
        json={"pin": "1234", "mnemonic": different_mnemonic},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "mnemonic_does_not_match_pubkey"


def test_setup_happy_path(app_client: TestClient) -> None:
    mnemonic = generate_mnemonic(12)
    _login_with_seed(app_client, mnemonic)
    r = app_client.post(
        "/auth/v2/pin/setup", json={"pin": "1234", "mnemonic": mnemonic}
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ============================================================ /recover


def test_recover_returns_seed_with_correct_pin(
    app_client: TestClient,
) -> None:
    """End-to-end: setup PIN, then anonymous /recover with the contact handle."""
    mnemonic = generate_mnemonic(12)
    user_id, _ = _login_with_seed(app_client, mnemonic)
    asyncio.run(_set_contact_handle(user_id, "@charlie"))

    # Setup PIN (still authenticated)
    r1 = app_client.post(
        "/auth/v2/pin/setup", json={"pin": "5678", "mnemonic": mnemonic}
    )
    assert r1.status_code == 200

    # Anonymous /recover
    app_client.cookies.clear()
    r2 = app_client.post(
        "/auth/v2/pin/recover",
        json={"contact_handle": "@charlie", "pin": "5678"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["mnemonic"] == mnemonic


def test_recover_wrong_pin_decrements_attempts(
    app_client: TestClient,
) -> None:
    mnemonic = generate_mnemonic(12)
    user_id, _ = _login_with_seed(app_client, mnemonic)
    asyncio.run(_set_contact_handle(user_id, "@bob"))
    app_client.post(
        "/auth/v2/pin/setup", json={"pin": "1111", "mnemonic": mnemonic}
    )
    app_client.cookies.clear()

    r = app_client.post(
        "/auth/v2/pin/recover",
        json={"contact_handle": "@bob", "pin": "0000"},
    )
    assert r.status_code == 401
    body = r.json()["detail"]
    assert body["reason"] == "wrong_pin"
    assert body["attempts_left"] == pin_module.MAX_FAILED_ATTEMPTS - 1


def test_recover_locks_after_5_attempts(app_client: TestClient) -> None:
    mnemonic = generate_mnemonic(12)
    user_id, _ = _login_with_seed(app_client, mnemonic)
    asyncio.run(_set_contact_handle(user_id, "@eve"))
    app_client.post(
        "/auth/v2/pin/setup", json={"pin": "2222", "mnemonic": mnemonic}
    )
    app_client.cookies.clear()

    # Fire 5 wrong PINs
    for _ in range(5):
        app_client.post(
            "/auth/v2/pin/recover",
            json={"contact_handle": "@eve", "pin": "9999"},
        )

    # 5th attempt or following → locked
    r = app_client.post(
        "/auth/v2/pin/recover",
        json={"contact_handle": "@eve", "pin": "2222"},  # CORRECT pin!
    )
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "locked"


def test_recover_unknown_contact_returns_404(app_client: TestClient) -> None:
    r = app_client.post(
        "/auth/v2/pin/recover",
        json={"contact_handle": "@nonexistent", "pin": "1234"},
    )
    assert r.status_code == 404


def test_recover_validates_pin_format(app_client: TestClient) -> None:
    r = app_client.post(
        "/auth/v2/pin/recover",
        json={"contact_handle": "@a", "pin": "abc"},
    )
    assert r.status_code == 422  # pydantic length=4


def test_recover_ip_rate_limit(app_client: TestClient) -> None:
    """11th request from same IP → 429."""
    for _ in range(10):
        app_client.post(
            "/auth/v2/pin/recover",
            json={"contact_handle": "@nobody", "pin": "0000"},
        )
    r = app_client.post(
        "/auth/v2/pin/recover",
        json={"contact_handle": "@nobody", "pin": "0000"},
    )
    assert r.status_code == 429


def test_recover_other_user_cant_get_my_seed(app_client: TestClient) -> None:
    """Knowing wrong PIN → no leak even if contact is right."""
    mnemonic = generate_mnemonic(12)
    user_id, _ = _login_with_seed(app_client, mnemonic)
    asyncio.run(_set_contact_handle(user_id, "@alice"))
    app_client.post(
        "/auth/v2/pin/setup", json={"pin": "8765", "mnemonic": mnemonic}
    )
    app_client.cookies.clear()

    r = app_client.post(
        "/auth/v2/pin/recover",
        json={"contact_handle": "@alice", "pin": "1234"},
    )
    assert r.status_code == 401
    assert "mnemonic" not in r.json().get("detail", {})


def test_recover_resets_attempts_on_success(app_client: TestClient) -> None:
    mnemonic = generate_mnemonic(12)
    user_id, _ = _login_with_seed(app_client, mnemonic)
    asyncio.run(_set_contact_handle(user_id, "@frank"))
    app_client.post(
        "/auth/v2/pin/setup", json={"pin": "1111", "mnemonic": mnemonic}
    )
    app_client.cookies.clear()

    # 3 wrong attempts
    for _ in range(3):
        app_client.post(
            "/auth/v2/pin/recover",
            json={"contact_handle": "@frank", "pin": "2222"},
        )

    # Now correct
    r = app_client.post(
        "/auth/v2/pin/recover",
        json={"contact_handle": "@frank", "pin": "1111"},
    )
    assert r.status_code == 200

    # Verify counter was reset
    from lcloud.db.base import get_sessionmaker
    from lcloud.db.models import User

    async def fetch() -> int:
        sm = get_sessionmaker()
        async with sm() as sess:
            u = (
                await sess.execute(sa.select(User).where(User.id == user_id))
            ).scalar_one()
            return int(u.pin_failed_attempts)

    assert asyncio.run(fetch()) == 0
