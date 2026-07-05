"""Tests for LCloud DB JSON document API."""

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


def _login(client: TestClient) -> int:
    mnemonic = generate_mnemonic(12)
    ident = derive_keypair(mnemonic)
    sk = SigningKey(ident.privkey_seed)

    r = client.post("/auth/v2/challenge", json={"pubkey": ident.pubkey.hex()})
    body = r.json()
    sig = sk.sign(bytes.fromhex(body["nonce"])).signature.hex()
    r2 = client.post(
        "/auth/v2/verify",
        json={"challenge_jwt": body["challenge_jwt"], "signature": sig},
    )
    assert r2.status_code == 200, r2.text
    return int(r2.json()["user_id"])


def test_json_db_crud_and_query(app_client: TestClient) -> None:
    _login(app_client)

    r = app_client.post("/api/v1/db/collections", json={"name": "users"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "users"

    alice = app_client.post(
        "/api/v1/db/users",
        json={
            "id": "alice",
            "data": {
                "name": "Alice",
                "role": "admin",
                "profile": {"city": "Tashkent"},
                "score": 10,
            },
        },
    )
    assert alice.status_code == 201, alice.text
    assert alice.json()["version"] == 1

    bob = app_client.post(
        "/api/v1/db/users",
        json={
            "id": "bob",
            "data": {
                "name": "Bob",
                "role": "user",
                "profile": {"city": "Samarkand"},
                "score": 5,
            },
        },
    )
    assert bob.status_code == 201, bob.text

    q = app_client.post(
        "/api/v1/db/users/query",
        json={
            "where": [{"field": "role", "op": "==", "value": "admin"}],
            "limit": 10,
        },
    )
    assert q.status_code == 200, q.text
    body = q.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "alice"

    q2 = app_client.post(
        "/api/v1/db/users/query",
        json={
            "where": [{"field": "profile.city", "op": "startsWith", "value": "Sam"}],
        },
    )
    assert q2.status_code == 200
    assert q2.json()["items"][0]["id"] == "bob"

    patched = app_client.patch(
        "/api/v1/db/users/alice",
        json={"data": {"role": "owner"}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["version"] == 2
    assert patched.json()["data"]["role"] == "owner"
    assert patched.json()["data"]["name"] == "Alice"

    deleted = app_client.delete("/api/v1/db/users/bob")
    assert deleted.status_code == 204
    missing = app_client.get("/api/v1/db/users/bob")
    assert missing.status_code == 404


def test_json_db_meta_exposes_machine_readable_limits(app_client: TestClient) -> None:
    r = app_client.get("/api/v1/db/_meta")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pagination"]["max_limit"] == 500
    assert body["query"]["max_where_filters"] == 20
    assert body["batch"]["max_writes"] == 100
    assert body["batch"]["atomic"] is True
    assert body["media"]["max_upload_bytes"] >= 1
    assert body["auth"]["v2_login_rate_limit"]["window_seconds"] == 300
    assert body["access_rules"]["rules"] == ["owner", "authenticated", "public"]


def test_json_db_isolated_per_user(app_client: TestClient) -> None:
    _login(app_client)
    assert app_client.post("/api/v1/db/collections", json={"name": "notes"}).status_code == 201
    assert (
        app_client.post(
            "/api/v1/db/notes",
            json={"id": "one", "data": {"title": "private"}},
        ).status_code
        == 201
    )

    app_client.cookies.clear()
    _login(app_client)
    r = app_client.get("/api/v1/db/collections")
    assert r.status_code == 200
    assert r.json() == []
    assert app_client.get("/api/v1/db/notes/one").status_code == 404


def test_json_db_public_access_rules(app_client: TestClient) -> None:
    _login(app_client)
    raw = app_client.post("/api/v1/keys", json={"label": "rules-test"}).json()["raw"]
    created = app_client.post("/api/v1/db/collections", json={"name": "public_posts"})
    assert created.status_code == 201, created.text
    collection_id = created.json()["id"]
    assert created.json()["read_rule"] == "owner"
    assert created.json()["write_rule"] == "owner"

    owner_doc = app_client.post(
        "/api/v1/db/public_posts",
        json={"id": "hello", "data": {"title": "Hello", "status": "published"}},
    )
    assert owner_doc.status_code == 201, owner_doc.text

    rules = app_client.get("/api/v1/db/collections/public_posts/rules")
    assert rules.status_code == 200
    assert rules.json()["read"] == "owner"
    assert rules.json()["public_base_path"] == f"/api/v1/public/db/{collection_id}"

    app_client.cookies.clear()
    private_read = app_client.get(f"/api/v1/public/db/{collection_id}/hello")
    assert private_read.status_code == 403
    private_write = app_client.post(
        f"/api/v1/public/db/{collection_id}",
        json={"id": "blocked", "data": {"title": "Blocked"}},
    )
    assert private_write.status_code == 403

    opened = app_client.put(
        "/api/v1/db/collections/public_posts/rules",
        headers={"Authorization": f"Bearer {raw}"},
        json={"read": "public", "write": "public"},
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["read"] == "public"
    assert opened.json()["write"] == "public"

    app_client.cookies.clear()
    public_read = app_client.get(f"/api/v1/public/db/{collection_id}/hello")
    assert public_read.status_code == 200, public_read.text
    assert public_read.json()["data"]["title"] == "Hello"

    public_query = app_client.post(
        f"/api/v1/public/db/{collection_id}/query",
        json={"where": [{"field": "status", "op": "==", "value": "published"}]},
    )
    assert public_query.status_code == 200, public_query.text
    assert public_query.json()["total"] == 1

    public_write = app_client.post(
        f"/api/v1/public/db/{collection_id}",
        json={"id": "from_browser", "data": {"title": "Browser"}},
    )
    assert public_write.status_code == 201, public_write.text
    assert public_write.json()["data"]["title"] == "Browser"


def test_json_db_bearer_api_key_auth(app_client: TestClient) -> None:
    _login(app_client)
    raw = app_client.post("/api/v1/keys", json={"label": "db-client"}).json()["raw"]
    app_client.post("/api/v1/db/collections", json={"name": "posts"})
    app_client.cookies.clear()

    r = app_client.post(
        "/api/v1/db/posts",
        headers={"Authorization": f"Bearer {raw}"},
        json={"id": "hello", "data": {"title": "Hello"}},
    )
    assert r.status_code == 201, r.text

    got = app_client.get(
        "/api/v1/db/posts/hello",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert got.status_code == 200
    assert got.json()["data"]["title"] == "Hello"


def test_json_db_batch_writes_are_atomic(app_client: TestClient) -> None:
    _login(app_client)
    assert app_client.post("/api/v1/db/collections", json={"name": "tasks"}).status_code == 201

    batch = app_client.post(
        "/api/v1/db/tasks/batch",
        json={
            "writes": [
                {"op": "create", "id": "one", "data": {"title": "One", "done": False}},
                {"op": "set", "id": "two", "data": {"title": "Two", "done": False}},
                {"op": "update", "id": "one", "data": {"done": True}},
            ]
        },
    )
    assert batch.status_code == 200, batch.text
    assert batch.json()["total"] == 3

    one = app_client.get("/api/v1/db/tasks/one")
    assert one.status_code == 200
    assert one.json()["data"] == {"title": "One", "done": True}
    assert one.json()["version"] == 2

    failed = app_client.post(
        "/api/v1/db/tasks/batch",
        json={
            "writes": [
                {"op": "set", "id": "three", "data": {"title": "Three"}},
                {"op": "update", "id": "missing", "data": {"done": True}},
            ]
        },
    )
    assert failed.status_code == 404
    assert app_client.get("/api/v1/db/tasks/three").status_code == 404


def test_json_db_create_can_reuse_deleted_document_id(app_client: TestClient) -> None:
    _login(app_client)
    assert app_client.post("/api/v1/db/collections", json={"name": "notes"}).status_code == 201
    assert (
        app_client.post(
            "/api/v1/db/notes",
            json={"id": "same", "data": {"title": "v1"}},
        ).status_code
        == 201
    )
    assert app_client.delete("/api/v1/db/notes/same").status_code == 204

    recreated = app_client.post(
        "/api/v1/db/notes",
        json={"id": "same", "data": {"title": "v2"}},
    )
    assert recreated.status_code == 201, recreated.text
    assert recreated.json()["data"]["title"] == "v2"
    assert recreated.json()["version"] == 3
