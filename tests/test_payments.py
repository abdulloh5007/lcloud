"""Tests for /api/v1/payments/* (public + admin)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from lcloud.auth.seed import derive_keypair, generate_mnemonic, is_valid_mnemonic


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
        asyncio.run(global_cache.clear())


def _new_user_keypair() -> tuple[str, SigningKey]:
    mnemonic = generate_mnemonic(12)
    ident = derive_keypair(mnemonic)
    return ident.pubkey.hex(), SigningKey(ident.privkey_seed)


def _login(client: TestClient, role: str = "user") -> int:
    """V2 login via challenge-response. Returns user_id."""
    pub_hex, sk = _new_user_keypair()
    r = client.post("/auth/v2/challenge", json={"pubkey": pub_hex})
    body = r.json()
    nonce = bytes.fromhex(body["nonce"])
    sig = sk.sign(nonce).signature.hex()
    r2 = client.post(
        "/auth/v2/verify",
        json={"challenge_jwt": body["challenge_jwt"], "signature": sig},
    )
    user_id = r2.json()["user_id"]

    if role == "admin":
        from lcloud.db.base import get_sessionmaker
        from lcloud.db.models import User

        async def promote() -> None:
            sm = get_sessionmaker()
            async with sm() as sess:
                await sess.execute(
                    sa.update(User).where(User.id == user_id).values(role="admin")
                )
                await sess.commit()

        asyncio.run(promote())

    return user_id


# -------------------------------------------------------- public endpoints


def test_payment_info_public(app_client: TestClient) -> None:
    r = app_client.get("/api/v1/payments/info")
    assert r.status_code == 200
    body = r.json()
    assert body["card_number"]
    assert body["amount_cents"] >= 100
    assert body["currency"]


def test_submit_request_creates_pending(app_client: TestClient) -> None:
    r = app_client.post(
        "/api/v1/payments/request",
        json={"contact_handle": "@alice", "note": "paid via bank"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    assert body["duplicate"] is False
    assert body["id"] >= 1


def test_submit_request_duplicate_returns_existing(
    app_client: TestClient,
) -> None:
    r1 = app_client.post(
        "/api/v1/payments/request", json={"contact_handle": "@bob"}
    )
    r2 = app_client.post(
        "/api/v1/payments/request", json={"contact_handle": "@bob"}
    )
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["duplicate"] is True


def test_submit_request_validates_input(app_client: TestClient) -> None:
    # Empty
    r = app_client.post("/api/v1/payments/request", json={"contact_handle": ""})
    assert r.status_code == 422
    # Too long
    r = app_client.post(
        "/api/v1/payments/request", json={"contact_handle": "x" * 200}
    )
    assert r.status_code == 422


# -------------------------------------------------------- admin endpoints


def test_list_requests_requires_admin(app_client: TestClient) -> None:
    # Anonymous
    r = app_client.get("/api/v1/admin/payments")
    assert r.status_code == 401

    # Regular user
    _login(app_client, role="user")
    r = app_client.get("/api/v1/admin/payments")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "admin_only"


def test_list_requests_as_admin(app_client: TestClient) -> None:
    # Submit one as anon
    app_client.post(
        "/api/v1/payments/request", json={"contact_handle": "@a"}
    )
    app_client.post(
        "/api/v1/payments/request", json={"contact_handle": "@b"}
    )

    # Login as admin
    _login(app_client, role="admin")
    r = app_client.get("/api/v1/admin/payments")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    assert items[0]["status"] == "pending"


def test_approve_generates_user_and_returns_seed(app_client: TestClient) -> None:
    # User submits request
    sub = app_client.post(
        "/api/v1/payments/request", json={"contact_handle": "@charlie"}
    ).json()
    req_id = sub["id"]

    # Admin approves
    _login(app_client, role="admin")
    r = app_client.post(f"/api/v1/admin/payments/{req_id}/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["request_id"] == req_id
    assert body["contact_handle"] == "@charlie"
    assert body["user_id"] >= 1
    assert "seed_phrase" in body

    # Seed phrase is a valid 24-word BIP39
    seed = body["seed_phrase"]
    assert len(seed.split()) == 24
    assert is_valid_mnemonic(seed)

    # Re-approving the same request → 409
    r2 = app_client.post(f"/api/v1/admin/payments/{req_id}/approve")
    assert r2.status_code == 409


def test_reject_request(app_client: TestClient) -> None:
    sub = app_client.post(
        "/api/v1/payments/request", json={"contact_handle": "@dave"}
    ).json()
    _login(app_client, role="admin")
    r = app_client.post(
        f"/api/v1/admin/payments/{sub['id']}/reject",
        json={"reason": "no payment received"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


def test_approved_user_can_login_with_returned_seed(
    app_client: TestClient,
) -> None:
    """Full flow: request → admin approve → use the seed to log in."""
    sub = app_client.post(
        "/api/v1/payments/request", json={"contact_handle": "@eve"}
    ).json()

    # Admin approves
    _login(app_client, role="admin")
    approval = app_client.post(
        f"/api/v1/admin/payments/{sub['id']}/approve"
    ).json()
    seed = approval["seed_phrase"]

    # Drop admin session
    app_client.cookies.clear()

    # Use seed to derive keypair and log in
    ident = derive_keypair(seed)
    sk = SigningKey(ident.privkey_seed)
    pub_hex = ident.pubkey.hex()

    r = app_client.post("/auth/v2/challenge", json={"pubkey": pub_hex})
    body = r.json()
    nonce = bytes.fromhex(body["nonce"])
    sig = sk.sign(nonce).signature.hex()
    r2 = app_client.post(
        "/auth/v2/verify",
        json={"challenge_jwt": body["challenge_jwt"], "signature": sig},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["registered"] is False  # user already exists from approval
    assert body2["user_id"] == approval["user_id"]
