"""End-to-end tests for V2 endpoints: /api/v1/clouds + /api/v1/files."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from lcloud.auth.seed import derive_keypair, generate_mnemonic
from lcloud.userbot.files import UploadResult


def _login_v2(client: TestClient) -> tuple[int, SigningKey]:
    """Register/login fresh user via V2 auth. Returns (user_id, sk)."""
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


@pytest.fixture
def app_with_userbot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Fresh app with mocked Telethon + admin Owner row.

    Mocks: create_cloud_chat, upload_file_to_cloud, delete_file_message,
    iter_download_file. Sets up a fake-authorized userbot manager.
    """
    db_file = tmp_path / "lcloud.db"
    monkeypatch.setenv("LC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LC_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "x")
    monkeypatch.setenv("LC_ADMIN_TG_ID", "42")
    monkeypatch.setenv("LC_COOKIE_SECURE", "false")
    monkeypatch.setenv("LC_MAX_FILE_BYTES", "10000000")  # 10 MB cap for tests

    from lcloud.api import auth_v2 as auth_v2_mod
    from lcloud.config import get_settings
    from lcloud.db import base as base_mod
    from lcloud.userbot.client import UserbotManager, set_userbot_manager

    get_settings.cache_clear()
    base_mod._engine = None
    base_mod._sessionmaker = None
    auth_v2_mod._v2_rate.reset()

    settings = get_settings()
    from tests.test_userbot import FakeTelegramClient

    fake_tg = FakeTelegramClient(me_id=42)

    mgr = UserbotManager(settings)
    monkeypatch.setattr(mgr, "_build_client", lambda: fake_tg)  # type: ignore[arg-type]
    set_userbot_manager(mgr)

    # Mock cloud creation (V2 router)
    next_chat_id = [-1_001_555_000_000]

    async def fake_create(client: Any, *, name: str, signing_key: Any) -> tuple[int, str, Any]:
        next_chat_id[0] += 1
        return next_chat_id[0], "LCLOUD1:fake", object()

    import lcloud.api.v2_clouds as v2_clouds_mod

    monkeypatch.setattr(v2_clouds_mod, "create_cloud_chat", fake_create)

    # Mock upload
    next_message_id = [1000]

    async def fake_upload(
        client: Any,
        *,
        chat_id: int,
        file_path: Path,
        original_name: str,
        sha256_digest: bytes,
        signing_key: Any,
    ) -> UploadResult:
        next_message_id[0] += 1
        return UploadResult(
            message_id=next_message_id[0],
            caption="LC1:{}",
            uploaded_at_unix=1700000000,
            signature=b"\x00" * 64,
        )

    async def fake_delete(client: Any, *, chat_id: int, message_id: int) -> None:
        return None

    import lcloud.api.v2_files as v2_files_mod

    monkeypatch.setattr(v2_files_mod, "upload_file_to_cloud", fake_upload)
    monkeypatch.setattr(v2_files_mod, "delete_file_message", fake_delete)

    from lcloud.main import create_app

    app = create_app()
    try:
        with TestClient(app) as client:
            # Force the userbot to be "started" + "authorized" so endpoints don't 503
            yield client
    finally:
        get_settings.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None
        set_userbot_manager(None)
        auth_v2_mod._v2_rate.reset()


def _login_admin_telegram(client: TestClient) -> None:
    """V1 admin login (phone+code) so we have an authorized userbot for V2 routes."""
    from lcloud.api.auth import get_login_rate_limiter

    get_login_rate_limiter().reset()
    r = client.post("/auth/telegram/start", json={"phone": "+1234567"})
    assert r.status_code == 200
    r = client.post("/auth/telegram/code", json={"code": "12345"})
    assert r.status_code == 200
    # Drop the V1 admin cookie so it doesn't interfere with V2 calls
    client.cookies.clear()


# -------------------------------------------------------------- /api/v1/clouds


def test_create_cloud_sets_owner_user_id(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    user_id, _ = _login_v2(app_with_userbot)

    r = app_with_userbot.post("/api/v1/clouds", json={"name": "MyCloud"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["owner_user_id"] == user_id
    assert body["name"] == "MyCloud"


def test_list_clouds_per_user_isolation(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)

    # User A creates 2
    _login_v2(app_with_userbot)
    app_with_userbot.post("/api/v1/clouds", json={"name": "A1"})
    app_with_userbot.post("/api/v1/clouds", json={"name": "A2"})

    # User B sees nothing
    app_with_userbot.cookies.clear()
    _login_v2(app_with_userbot)
    r = app_with_userbot.get("/api/v1/clouds")
    assert r.json() == []

    # User B creates 1
    app_with_userbot.post("/api/v1/clouds", json={"name": "B1"})
    rb = app_with_userbot.get("/api/v1/clouds").json()
    assert len(rb) == 1
    assert rb[0]["name"] == "B1"


def test_delete_other_users_cloud_forbidden(
    app_with_userbot: TestClient,
) -> None:
    _login_admin_telegram(app_with_userbot)

    # User A creates
    _login_v2(app_with_userbot)
    cloud = app_with_userbot.post("/api/v1/clouds", json={"name": "A"}).json()
    cloud_id = cloud["id"]

    # User B tries to delete
    app_with_userbot.cookies.clear()
    _login_v2(app_with_userbot)
    r = app_with_userbot.delete(f"/api/v1/clouds/{cloud_id}")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "forbidden"


def test_clouds_require_auth(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    r = app_with_userbot.get("/api/v1/clouds")
    assert r.status_code == 401


# -------------------------------------------------------------- /api/v1/files


def _create_cloud(client: TestClient) -> int:
    return int(client.post("/api/v1/clouds", json={"name": "C"}).json()["id"])


def test_upload_increments_quota(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    user_id, _ = _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)

    payload = b"hello world" * 10  # 110 bytes
    r = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("test.txt", io.BytesIO(payload), "text/plain")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["owner_user_id"] == user_id
    assert body["size"] == len(payload)

    # Quota endpoint reflects the upload
    rq = app_with_userbot.get("/api/v1/files/quota")
    assert rq.status_code == 200
    q = rq.json()
    assert q["used_bytes"] == len(payload)
    assert q["free_bytes"] == q["quota_bytes"] - len(payload)


def test_upload_rejected_when_over_quota(
    app_with_userbot: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login_admin_telegram(app_with_userbot)
    user_id, _ = _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)

    # Force quota tiny
    import asyncio

    import sqlalchemy as sa

    from lcloud.db.base import get_sessionmaker
    from lcloud.db.models import User

    async def shrink_quota() -> None:
        sm = get_sessionmaker()
        async with sm() as sess:
            await sess.execute(
                sa.update(User).where(User.id == user_id).values(storage_quota_bytes=10)
            )
            await sess.commit()

    asyncio.run(shrink_quota())

    payload = b"way too much data for a 10-byte quota"
    r = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("big.txt", io.BytesIO(payload), "text/plain")},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["reason"] == "quota_exceeded"

    # Quota usage should still be 0 — upload was rejected before persistence
    rq = app_with_userbot.get("/api/v1/files/quota").json()
    assert rq["used_bytes"] == 0


def test_files_isolated_per_user(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)

    # User A uploads to their cloud
    _login_v2(app_with_userbot)
    cloud_a = _create_cloud(app_with_userbot)
    app_with_userbot.post(
        f"/api/v1/clouds/{cloud_a}/files",
        files={"file": ("a.txt", io.BytesIO(b"aaa"), "text/plain")},
    )

    # User B can NOT list user A's cloud files
    app_with_userbot.cookies.clear()
    _login_v2(app_with_userbot)
    r = app_with_userbot.get(f"/api/v1/clouds/{cloud_a}/files")
    assert r.status_code == 403


def test_delete_decrements_quota(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)

    payload = b"content" * 20  # 140 bytes
    r = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("x.txt", io.BytesIO(payload), "text/plain")},
    )
    file_id = r.json()["id"]

    q1 = app_with_userbot.get("/api/v1/files/quota").json()
    assert q1["used_bytes"] == len(payload)

    rd = app_with_userbot.delete(f"/api/v1/files/{file_id}")
    assert rd.status_code == 204

    q2 = app_with_userbot.get("/api/v1/files/quota").json()
    assert q2["used_bytes"] == 0


def test_delete_other_users_file_forbidden(
    app_with_userbot: TestClient,
) -> None:
    _login_admin_telegram(app_with_userbot)

    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)
    upload = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("a.txt", io.BytesIO(b"a"), "text/plain")},
    ).json()
    file_id = upload["id"]

    app_with_userbot.cookies.clear()
    _login_v2(app_with_userbot)
    r = app_with_userbot.delete(f"/api/v1/files/{file_id}")
    assert r.status_code == 403


def test_admin_sees_all_files(app_with_userbot: TestClient) -> None:
    _login_admin_telegram(app_with_userbot)

    # A regular user uploads
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)
    app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("a.txt", io.BytesIO(b"abc"), "text/plain")},
    )

    # Admin user (manually promote in DB) then lists clouds
    import asyncio

    import sqlalchemy as sa

    from lcloud.db.base import get_sessionmaker
    from lcloud.db.models import User

    app_with_userbot.cookies.clear()
    _login_v2(app_with_userbot)
    me = app_with_userbot.get("/auth/v2/me").json()

    async def promote() -> None:
        sm = get_sessionmaker()
        async with sm() as sess:
            await sess.execute(
                sa.update(User).where(User.id == me["user_id"]).values(role="admin")
            )
            await sess.commit()

    asyncio.run(promote())

    # Admin sees all clouds
    r = app_with_userbot.get("/api/v1/clouds")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_quota_endpoint_returns_zero_for_new_user(
    app_with_userbot: TestClient,
) -> None:
    _login_v2(app_with_userbot)
    r = app_with_userbot.get("/api/v1/files/quota")
    assert r.status_code == 200
    assert r.json()["used_bytes"] == 0
    assert r.json()["quota_bytes"] >= 1024**3



# -------------------------------------------------------------- LC2 client signing


def test_upload_with_lc2_client_signature_succeeds(
    app_with_userbot: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client signs sha256||ts||pubkey, server verifies and writes LC2 caption."""
    import hashlib
    import time

    from lcloud.crypto.lc2 import canonical_payload
    from lcloud.userbot.files_lc2 import Lc2UploadResult

    next_msg = [5000]

    async def fake_upload_lc2(client: Any, *, chat_id: int, file_path: Any, original_name: str, payload: Any) -> Lc2UploadResult:
        next_msg[0] += 1
        return Lc2UploadResult(
            message_id=next_msg[0],
            caption=payload.to_caption(),
            payload=payload,
        )

    import lcloud.api.v2_files as v2_files_mod
    monkeypatch.setattr(v2_files_mod, "upload_file_lc2", fake_upload_lc2)

    _login_admin_telegram(app_with_userbot)
    _, sk = _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)

    payload_bytes = b"hello LC2 world" * 5
    sha256 = hashlib.sha256(payload_bytes).digest()
    ts = int(time.time())
    pub = bytes(sk.verify_key)
    sig = sk.sign(canonical_payload(sha256=sha256, ts=ts, pubkey=pub)).signature

    r = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("a.bin", io.BytesIO(payload_bytes), "application/octet-stream")},
        data={
            "client_sha256": sha256.hex(),
            "signature": sig.hex(),
            "ts": str(ts),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["caption_kind"] == "LC2"
    assert body["size"] == len(payload_bytes)


def test_upload_with_lc2_wrong_sha_rejected(app_with_userbot: TestClient) -> None:
    """Client claims a sha256 that doesn't match the file → 400."""
    import hashlib
    import time

    from lcloud.crypto.lc2 import canonical_payload

    _login_admin_telegram(app_with_userbot)
    _, sk = _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)

    payload_bytes = b"actual content"
    fake_sha = hashlib.sha256(b"different content").digest()
    ts = int(time.time())
    pub = bytes(sk.verify_key)
    sig = sk.sign(canonical_payload(sha256=fake_sha, ts=ts, pubkey=pub)).signature

    r = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("x.bin", io.BytesIO(payload_bytes), "application/octet-stream")},
        data={
            "client_sha256": fake_sha.hex(),
            "signature": sig.hex(),
            "ts": str(ts),
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "lc2_sha256_mismatch"


def test_upload_with_lc2_bad_signature_rejected(
    app_with_userbot: TestClient,
) -> None:
    """Wrong key signs the payload → server rejects."""
    import hashlib
    import time

    from lcloud.auth.seed import derive_keypair, generate_mnemonic
    from lcloud.crypto.lc2 import canonical_payload

    _login_admin_telegram(app_with_userbot)
    _, sk = _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)

    # OTHER user's keypair
    other_ident = derive_keypair(generate_mnemonic(12))
    other_sk = SigningKey(other_ident.privkey_seed)

    payload = b"some bytes"
    sha = hashlib.sha256(payload).digest()
    ts = int(time.time())
    pub = bytes(sk.verify_key)
    bad_sig = other_sk.sign(canonical_payload(sha256=sha, ts=ts, pubkey=pub)).signature

    r = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("x.bin", io.BytesIO(payload), "application/octet-stream")},
        data={
            "client_sha256": sha.hex(),
            "signature": bad_sig.hex(),
            "ts": str(ts),
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "lc2_verify_failed"


def test_upload_falls_back_to_lc1_when_no_signature(
    app_with_userbot: TestClient,
) -> None:
    """No client_sha256/signature/ts → server signs with admin key (LC1)."""
    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)

    r = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("legacy.txt", io.BytesIO(b"legacy"), "text/plain")},
    )
    assert r.status_code == 201
    assert r.json()["caption_kind"] == "LC1"



# -------------------------------------------------------------- compression


def test_upload_default_compresses_jpeg(
    app_with_userbot: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Image upload re-encodes by default, server stores compressed bytes."""
    from PIL import Image

    from lcloud.userbot.files import UploadResult

    next_msg = [9000]
    captured_size: dict[str, int] = {}

    async def fake_upload(client, *, chat_id, file_path, original_name, sha256_digest, signing_key):
        captured_size["bytes"] = file_path.stat().st_size
        next_msg[0] += 1
        return UploadResult(
            message_id=next_msg[0],
            caption="LC1:{}",
            uploaded_at_unix=1700000000,
            signature=b"\x00" * 64,
        )

    import lcloud.api.v2_files as v2_files_mod
    monkeypatch.setattr(v2_files_mod, "upload_file_to_cloud", fake_upload)

    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)

    # Make a high-quality JPEG buffer with random noise so it has substance
    import secrets
    sz = 1200
    raw_pixels = bytearray(sz * sz * 3)
    for i in range(0, len(raw_pixels), 4096):
        raw_pixels[i:i + 4096] = secrets.token_bytes(min(4096, len(raw_pixels) - i))
    img = Image.frombytes("RGB", (sz, sz), bytes(raw_pixels))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=98)
    raw = buf.getvalue()
    original_size = len(raw)

    r = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("photo.jpg", io.BytesIO(raw), "image/jpeg")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["compressed"] is True
    assert body["size"] < original_size
    assert body["original_size_bytes"] == original_size
    assert body["compression_ratio"] < 1.0
    # The bytes that landed in TG (captured by our fake) should be the compressed ones
    assert captured_size["bytes"] == body["size"]


def test_upload_compress_false_keeps_original(
    app_with_userbot: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """compress=false → bytes go to TG unchanged."""
    from PIL import Image

    from lcloud.userbot.files import UploadResult

    next_msg = [9100]
    captured_size: dict[str, int] = {}

    async def fake_upload(client, *, chat_id, file_path, original_name, sha256_digest, signing_key):
        captured_size["bytes"] = file_path.stat().st_size
        next_msg[0] += 1
        return UploadResult(
            message_id=next_msg[0],
            caption="LC1:{}",
            uploaded_at_unix=1700000000,
            signature=b"\x00" * 64,
        )

    import lcloud.api.v2_files as v2_files_mod
    monkeypatch.setattr(v2_files_mod, "upload_file_to_cloud", fake_upload)

    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)

    import secrets
    sz = 1200
    raw_pixels = bytearray(sz * sz * 3)
    for i in range(0, len(raw_pixels), 4096):
        raw_pixels[i:i + 4096] = secrets.token_bytes(min(4096, len(raw_pixels) - i))
    img = Image.frombytes("RGB", (sz, sz), bytes(raw_pixels))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=98)
    raw = buf.getvalue()
    original_size = len(raw)

    r = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("photo.jpg", io.BytesIO(raw), "image/jpeg")},
        data={"compress": "false"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["compressed"] is False
    assert body["size"] == original_size
    assert body.get("original_size_bytes") is None
    assert captured_size["bytes"] == original_size


def test_upload_non_image_format_ignored_for_compression(
    app_with_userbot: TestClient,
) -> None:
    """Text/binary files: compress flag is ignored, byte-for-byte upload."""
    _login_admin_telegram(app_with_userbot)
    _login_v2(app_with_userbot)
    cloud_id = _create_cloud(app_with_userbot)

    payload = b"plain text " * 10_000  # 110 KiB, big enough to exceed threshold
    r = app_with_userbot.post(
        f"/api/v1/clouds/{cloud_id}/files",
        files={"file": ("doc.txt", io.BytesIO(payload), "text/plain")},
        data={"compress": "true"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["compressed"] is False
    assert body["size"] == len(payload)
