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

    from lcloud.api import app_auth as app_auth_mod
    from lcloud.api import auth_v2 as auth_v2_mod
    from lcloud.api import json_db as json_db_mod
    from lcloud.config import get_settings
    from lcloud.db import base as base_mod
    from lcloud.userbot.client import set_userbot_manager

    get_settings.cache_clear()
    base_mod._engine = None
    base_mod._sessionmaker = None
    set_userbot_manager(None)
    auth_v2_mod._v2_rate.reset()
    app_auth_mod.reset_app_auth_rate_limit()
    json_db_mod.reset_json_db_public_rate_limits()

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
        app_auth_mod.reset_app_auth_rate_limit()
        json_db_mod.reset_json_db_public_rate_limits()


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
    assert body["access_rules"]["rules"] == [
        "owner",
        "document_owner",
        "authenticated",
        "public",
    ]
    assert body["access_rules"]["public_read_rate_limit"]["capacity"] == 120
    assert body["access_rules"]["public_write_rate_limit"]["capacity"] == 30
    assert "max_bytes" in body["access_rules"]["write_validator"]["fields"]
    assert body["realtime"]["transport"] == "sse"
    assert body["realtime"]["event"] == "lcloud.db.change"


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


def test_json_db_publishable_key_public_browser_flow(app_client: TestClient) -> None:
    _login(app_client)

    key_r = app_client.post("/api/v1/db/public-keys", json={"label": "web"})
    assert key_r.status_code == 201, key_r.text
    key_body = key_r.json()
    assert key_body["key"].startswith("lcpk_")
    assert key_body["prefix"] == key_body["key"][:13]

    keys = app_client.get("/api/v1/db/public-keys")
    assert keys.status_code == 200
    assert keys.json()[0]["key"] == key_body["key"]

    owner_api_key = app_client.post("/api/v1/keys", json={"label": "owner"}).json()[
        "raw"
    ]
    created = app_client.post(
        "/api/v1/db/collections", json={"name": "website_posts"}
    )
    assert created.status_code == 201, created.text
    rules = app_client.put(
        "/api/v1/db/collections/website_posts/rules",
        json={"read": "public", "write": "public"},
    )
    assert rules.status_code == 200, rules.text
    validator = app_client.put(
        "/api/v1/db/collections/website_posts/validator",
        json={
            "max_bytes": 200,
            "max_fields": 2,
            "required_fields": ["title"],
            "allowed_fields": ["title", "status"],
        },
    )
    assert validator.status_code == 200, validator.text

    app_client.cookies.clear()
    public_create = app_client.post(
        f"/api/v1/public/db/key/{key_body['key']}/website_posts",
        json={"id": "hello", "data": {"title": "Hello", "status": "published"}},
    )
    assert public_create.status_code == 201, public_create.text

    public_list = app_client.get(
        f"/api/v1/public/db/key/{key_body['key']}/website_posts"
    )
    assert public_list.status_code == 200, public_list.text
    assert public_list.json()["items"][0]["id"] == "hello"

    public_get = app_client.get(
        f"/api/v1/public/db/key/{key_body['key']}/website_posts/hello"
    )
    assert public_get.status_code == 200
    assert public_get.json()["data"]["title"] == "Hello"

    revoked = app_client.delete(
        f"/api/v1/db/public-keys/{key_body['id']}",
        headers={"Authorization": f"Bearer {owner_api_key}"},
    )
    assert revoked.status_code == 200

    app_client.cookies.clear()
    blocked = app_client.get(
        f"/api/v1/public/db/key/{key_body['key']}/website_posts"
    )
    assert blocked.status_code == 404


def test_app_auth_refresh_and_document_owner_rules(app_client: TestClient) -> None:
    _login(app_client)
    public_key = app_client.post(
        "/api/v1/db/public-keys", json={"label": "app-auth"}
    ).json()["key"]
    assert app_client.post(
        "/api/v1/db/collections", json={"name": "private_notes"}
    ).status_code == 201
    rules = app_client.put(
        "/api/v1/db/collections/private_notes/rules",
        json={"read": "document_owner", "write": "document_owner"},
    )
    assert rules.status_code == 200, rules.text
    app_client.cookies.clear()

    first = app_client.post(f"/api/v1/public/auth/key/{public_key}/anonymous")
    assert first.status_code == 201, first.text
    first_session = first.json()
    first_headers = {"Authorization": f"Bearer {first_session['access_token']}"}
    first_uid = first_session["user"]["uid"]

    created = app_client.post(
        f"/api/v1/public/db/key/{public_key}/private_notes",
        headers=first_headers,
        json={"id": "first", "data": {"text": "private"}},
    )
    assert created.status_code == 201, created.text
    assert created.json()["owner_id"] == first_uid

    second = app_client.post(f"/api/v1/public/auth/key/{public_key}/anonymous").json()
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    assert app_client.post(
        f"/api/v1/public/db/key/{public_key}/private_notes",
        headers=second_headers,
        json={"id": "second", "data": {"text": "also private"}},
    ).status_code == 201

    first_list = app_client.get(
        f"/api/v1/public/db/key/{public_key}/private_notes",
        headers=first_headers,
    )
    assert first_list.status_code == 200, first_list.text
    assert [item["id"] for item in first_list.json()["items"]] == ["first"]
    forbidden = app_client.patch(
        f"/api/v1/public/db/key/{public_key}/private_notes/second",
        headers=first_headers,
        json={"data": {"text": "stolen"}},
    )
    assert forbidden.status_code == 403

    app_client.cookies.clear()
    _login(app_client)
    other_key = app_client.post(
        "/api/v1/db/public-keys", json={"label": "other-project"}
    ).json()["key"]
    assert app_client.post(
        "/api/v1/db/collections", json={"name": "members"}
    ).status_code == 201
    assert app_client.put(
        "/api/v1/db/collections/members/rules",
        json={"read": "authenticated", "write": "authenticated"},
    ).status_code == 200
    app_client.cookies.clear()
    cross_project = app_client.post(
        f"/api/v1/public/db/key/{other_key}/members",
        headers=first_headers,
        json={"id": "intruder", "data": {"name": "blocked"}},
    )
    assert cross_project.status_code == 403

    refreshed = app_client.post(
        f"/api/v1/public/auth/key/{public_key}/refresh",
        json={"refresh_token": first_session["refresh_token"]},
    )
    assert refreshed.status_code == 200, refreshed.text
    refreshed_body = refreshed.json()
    assert refreshed_body["refresh_token"] == first_session["refresh_token"]
    assert app_client.get(
        f"/api/v1/public/auth/key/{public_key}/me",
        headers={"Authorization": f"Bearer {refreshed_body['access_token']}"},
    ).json()["uid"] == first_uid
    reused = app_client.post(
        f"/api/v1/public/auth/key/{public_key}/refresh",
        json={"refresh_token": first_session["refresh_token"]},
    )
    assert reused.status_code == 200
    signed_out = app_client.post(
        f"/api/v1/public/auth/key/{public_key}/sign-out",
        json={"refresh_token": first_session["refresh_token"]},
    )
    assert signed_out.status_code == 204
    assert app_client.post(
        f"/api/v1/public/auth/key/{public_key}/refresh",
        json={"refresh_token": first_session["refresh_token"]},
    ).status_code == 401

def test_json_db_sse_events_once(app_client: TestClient) -> None:
    _login(app_client)
    assert app_client.post("/api/v1/db/collections", json={"name": "events"}).status_code == 201
    created = app_client.post(
        "/api/v1/db/events",
        json={"id": "one", "data": {"title": "One"}},
    )
    assert created.status_code == 201, created.text

    events = app_client.get("/api/v1/db/events/events?since=0&once=true")
    assert events.status_code == 200, events.text
    assert "text/event-stream" in events.headers["content-type"]
    body = events.text
    assert "event: lcloud.db.change" in body
    assert '"doc_id":"one"' in body


def test_json_db_public_sse_events_once(app_client: TestClient) -> None:
    _login(app_client)
    raw = app_client.post("/api/v1/keys", json={"label": "events-test"}).json()["raw"]
    created = app_client.post("/api/v1/db/collections", json={"name": "public_events"})
    assert created.status_code == 201, created.text
    collection_id = created.json()["id"]
    assert (
        app_client.put(
            "/api/v1/db/collections/public_events/rules",
            headers={"Authorization": f"Bearer {raw}"},
            json={"read": "public", "write": "owner"},
        ).status_code
        == 200
    )
    assert (
        app_client.post(
            "/api/v1/db/public_events",
            headers={"Authorization": f"Bearer {raw}"},
            json={"id": "visible", "data": {"title": "Visible"}},
        ).status_code
        == 201
    )

    app_client.cookies.clear()
    events = app_client.get(f"/api/v1/public/db/{collection_id}/events?since=0&once=true")
    assert events.status_code == 200, events.text
    assert "event: lcloud.db.change" in events.text
    assert '"doc_id":"visible"' in events.text


def test_json_db_public_write_validator(app_client: TestClient) -> None:
    _login(app_client)
    raw = app_client.post("/api/v1/keys", json={"label": "validator-test"}).json()["raw"]
    created = app_client.post("/api/v1/db/collections", json={"name": "leads"})
    assert created.status_code == 201, created.text
    collection_id = created.json()["id"]

    opened = app_client.put(
        "/api/v1/db/collections/leads/rules",
        headers={"Authorization": f"Bearer {raw}"},
        json={"read": "owner", "write": "public"},
    )
    assert opened.status_code == 200, opened.text

    validator = app_client.put(
        "/api/v1/db/collections/leads/validator",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "max_bytes": 80,
            "max_fields": 2,
            "required_fields": ["email"],
            "allowed_fields": ["email", "message"],
        },
    )
    assert validator.status_code == 200, validator.text
    assert validator.json()["validator"]["required_fields"] == ["email"]

    app_client.cookies.clear()
    missing = app_client.post(
        f"/api/v1/public/db/{collection_id}",
        json={"id": "missing", "data": {"message": "hi"}},
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["check"] == "required_fields"

    extra = app_client.post(
        f"/api/v1/public/db/{collection_id}",
        json={"id": "extra", "data": {"email": "a@b.co", "role": "admin"}},
    )
    assert extra.status_code == 422
    assert extra.json()["detail"]["check"] == "allowed_fields"

    too_large = app_client.post(
        f"/api/v1/public/db/{collection_id}",
        json={"id": "large", "data": {"email": "a@b.co", "message": "x" * 200}},
    )
    assert too_large.status_code == 422
    assert too_large.json()["detail"]["check"] == "max_bytes"

    ok = app_client.post(
        f"/api/v1/public/db/{collection_id}",
        json={"id": "ok", "data": {"email": "a@b.co", "message": "hi"}},
    )
    assert ok.status_code == 201, ok.text

    bad_patch = app_client.patch(
        f"/api/v1/public/db/{collection_id}/ok",
        json={"data": {"role": "admin"}},
    )
    assert bad_patch.status_code == 422


def test_json_db_public_write_rate_limit(app_client: TestClient) -> None:
    _login(app_client)
    raw = app_client.post("/api/v1/keys", json={"label": "rate-test"}).json()["raw"]
    created = app_client.post("/api/v1/db/collections", json={"name": "open_forms"})
    assert created.status_code == 201, created.text
    collection_id = created.json()["id"]
    assert (
        app_client.put(
            "/api/v1/db/collections/open_forms/rules",
            headers={"Authorization": f"Bearer {raw}"},
            json={"read": "owner", "write": "public"},
        ).status_code
        == 200
    )

    app_client.cookies.clear()
    for index in range(30):
        r = app_client.post(
            f"/api/v1/public/db/{collection_id}",
            json={"id": f"lead_{index}", "data": {"email": f"{index}@example.com"}},
        )
        assert r.status_code == 201, r.text

    limited = app_client.post(
        f"/api/v1/public/db/{collection_id}",
        json={"id": "lead_limited", "data": {"email": "limited@example.com"}},
    )
    assert limited.status_code == 429
    assert limited.json()["detail"]["scope"] == "public_write"


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



def test_json_db_backup_status_reports_lag(app_client: TestClient) -> None:
    _login(app_client)
    assert app_client.post("/api/v1/db/collections", json={"name": "backup_notes"}).status_code == 201
    assert (
        app_client.post(
            "/api/v1/db/backup_notes",
            json={"id": "one", "data": {"title": "One"}},
        ).status_code
        == 201
    )

    status = app_client.get("/api/v1/db/backup/status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["enabled"] is True
    assert body["format"] == "lcloud-json-db-segment-v1"
    assert body["last_local_operation_id"] >= 2
    assert body["last_backed_up_operation_id"] == 0
    assert body["lag_operations"] == body["last_local_operation_id"]


def test_json_db_backup_once_uploads_segment(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from lcloud.userbot import db_backup as db_backup_mod
    from lcloud.userbot.client import UserbotManager, set_userbot_manager

    _login(app_client)
    assert app_client.post("/api/v1/db/collections", json={"name": "backup_run"}).status_code == 201
    assert (
        app_client.post(
            "/api/v1/db/backup_run",
            json={"id": "one", "data": {"title": "One"}},
        ).status_code
        == 201
    )

    class FakeMessage:
        id = 777

    class FakeTelegramClient:
        def __init__(self) -> None:
            self.captions: list[str] = []

        async def send_file(self, entity, *, file, force_document, caption, attributes):
            assert entity == "me"
            assert force_document is True
            assert caption.startswith("LCDB1:")
            self.captions.append(caption)
            return FakeMessage()

    from lcloud.config import get_settings

    settings = get_settings()
    manager = UserbotManager(settings)
    manager._client = FakeTelegramClient()  # type: ignore[assignment]
    monkeypatch.setattr(manager, "is_admin_authorized", lambda: asyncio.sleep(0, True))
    set_userbot_manager(manager)

    uploaded = asyncio.run(db_backup_mod.run_json_db_backup_once(settings))
    assert uploaded == 1

    status = app_client.get("/api/v1/db/backup/status").json()
    assert status["lag_operations"] == 0
    assert status["last_segment"]["telegram_message_id"] == 777
    assert status["last_segment"]["operation_count"] >= 2


def test_json_db_restore_replays_lcdb1_segment(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import contextlib
    import json

    import sqlalchemy as sa

    from lcloud.config import get_settings
    from lcloud.db.base import get_sessionmaker
    from lcloud.db.models import JsonBackupSegment, JsonBackupState, JsonCollection
    from lcloud.userbot import db_backup as db_backup_mod
    from lcloud.userbot.db_restore import restore_json_db_from_telegram

    user_id = _login(app_client)
    assert app_client.post("/api/v1/db/collections", json={"name": "restore_notes"}).status_code == 201
    assert (
        app_client.post(
            "/api/v1/db/restore_notes",
            json={"id": "one", "data": {"title": "One", "done": False}},
        ).status_code
        == 201
    )
    assert (
        app_client.patch(
            "/api/v1/db/restore_notes/one",
            json={"data": {"done": True}},
        ).status_code
        == 200
    )

    settings = get_settings()
    built = asyncio.run(
        db_backup_mod._build_segment_file(  # type: ignore[attr-defined]
            owner_user_id=user_id,
            settings=settings,
            batch_limit=100,
        )
    )
    assert built is not None
    path, meta = built
    compressed = path.read_bytes()
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    caption = "LCDB1:" + json.dumps(
        {
            "f": "lcloud-json-db-segment-v1",
            "u": meta["owner_user_id"],
            "a": meta["first_operation_id"],
            "b": meta["last_operation_id"],
            "n": meta["operation_count"],
            "h": meta["sha256"],
        },
        separators=(",", ":"),
    )

    async def clear_json_db() -> None:
        sm = get_sessionmaker()
        async with sm() as sess:
            await sess.execute(sa.delete(JsonBackupSegment))
            await sess.execute(sa.delete(JsonBackupState))
            await sess.execute(
                sa.delete(JsonCollection).where(JsonCollection.owner_user_id == user_id)
            )
            await sess.commit()

    asyncio.run(clear_json_db())
    assert app_client.get("/api/v1/db/restore_notes/one").status_code == 404

    class FakeMessage:
        id = 999
        message = caption

    class FakeClient:
        async def iter_messages(self, entity, *, search, **kwargs):
            assert entity == "me"
            assert search == "LCDB1:"
            yield FakeMessage()

        async def download_media(self, message, *, file):
            assert message.id == 999
            assert file is bytes
            return compressed

    result = asyncio.run(
        restore_json_db_from_telegram(FakeClient(), target_owner_user_id=user_id)
    )
    assert result.segments == 1
    assert result.operations >= 3

    restored = app_client.get("/api/v1/db/restore_notes/one")
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"] == {"title": "One", "done": True}
    status = app_client.get("/api/v1/db/backup/status").json()
    assert status["last_backed_up_operation_id"] == meta["last_operation_id"]
    assert status["last_segment"]["status"] == "restored"
