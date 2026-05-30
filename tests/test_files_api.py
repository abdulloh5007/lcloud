"""End-to-end tests for /clouds/{id}/files and /files/{id}/* via TestClient."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lcloud.config import Settings
from lcloud.userbot.client import UserbotManager
from lcloud.userbot.files import UploadResult
from tests.test_files_userbot import FakeTGClient
from tests.test_userbot import FakeTelegramClient


def _bootstrap_isolated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Settings:
    monkeypatch.setenv("LC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LC_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'lcloud.db'}")
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "testhash")
    monkeypatch.setenv("LC_ADMIN_TG_ID", "42")
    monkeypatch.setenv("LC_COOKIE_SECURE", "false")
    monkeypatch.setenv("LC_MAX_FILE_BYTES", "10000")  # 10 KB cap for tests

    from lcloud.config import get_settings
    from lcloud.db import base as base_mod

    get_settings.cache_clear()
    base_mod._engine = None
    base_mod._sessionmaker = None
    return get_settings()


@pytest.fixture
def authed_app_with_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, int]]:
    """Authenticated TestClient where one cloud row already exists. Returns
    (client, cloud_id). Telethon `create_cloud_chat` is mocked."""
    settings = _bootstrap_isolated_env(tmp_path, monkeypatch)

    fake_login = FakeTelegramClient(me_id=42)
    mgr = UserbotManager(settings)
    monkeypatch.setattr(mgr, "_build_client", lambda: fake_login)  # type: ignore[arg-type]

    from lcloud.api.auth import get_login_rate_limiter
    from lcloud.userbot.client import set_userbot_manager

    set_userbot_manager(mgr)
    get_login_rate_limiter().reset()

    # Mock create_cloud_chat to avoid Telethon
    async def fake_create(
        client: Any, *, name: str, signing_key: Any
    ) -> tuple[int, str, Any]:
        return -1_001_111_111_111, "LCLOUD1:fake", object()

    import lcloud.api.clouds as clouds_mod

    monkeypatch.setattr(clouds_mod, "create_cloud_chat", fake_create)

    from lcloud.config import get_settings as _gs
    from lcloud.db import base as base_mod
    from lcloud.main import create_app

    app = create_app()
    try:
        with TestClient(app) as client:
            r = client.post("/auth/telegram/start", json={"phone": "+1234567"})
            assert r.status_code == 200
            r = client.post("/auth/telegram/code", json={"code": "12345"})
            assert r.status_code == 200

            r = client.post("/clouds", json={"name": "Photos"})
            assert r.status_code == 201
            cloud_id = r.json()["id"]
            yield client, cloud_id
    finally:
        set_userbot_manager(None)
        _gs.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None


def test_list_files_empty(
    authed_app_with_cloud: tuple[TestClient, int],
) -> None:
    client, cloud_id = authed_app_with_cloud
    r = client.get(f"/clouds/{cloud_id}/files")
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_upload_then_list_then_delete(
    authed_app_with_cloud: tuple[TestClient, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, cloud_id = authed_app_with_cloud

    # Mock upload_file_to_cloud + delete_file_message — we don't need real TG
    captured: dict[str, Any] = {}

    async def fake_upload(
        cli: Any, *, chat_id: int, file_path: Path,
        original_name: str, sha256_digest: bytes, signing_key: Any,
    ) -> UploadResult:
        captured["chat_id"] = chat_id
        captured["original_name"] = original_name
        captured["size"] = file_path.stat().st_size
        captured["sha256"] = sha256_digest
        return UploadResult(
            message_id=777,
            caption="LC1:fake",
            uploaded_at_unix=1700000000,
            signature=b"\x00" * 64,
        )

    async def fake_delete(cli: Any, *, chat_id: int, message_id: int) -> None:
        captured.setdefault("deletions", []).append((chat_id, message_id))

    import lcloud.api.files as files_mod

    monkeypatch.setattr(files_mod, "upload_file_to_cloud", fake_upload)
    monkeypatch.setattr(files_mod, "delete_file_message", fake_delete)

    payload = b"hello world\n" * 100
    r = client.post(
        f"/clouds/{cloud_id}/files",
        files={"file": ("hello.txt", payload, "text/plain")},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "hello.txt"
    assert body["size"] == len(payload)
    assert body["mime"] == "text/plain"
    file_id = body["id"]

    # Captured values are correct
    assert captured["chat_id"] == -1_001_111_111_111
    assert captured["original_name"] == "hello.txt"
    assert captured["size"] == len(payload)
    assert captured["sha256"] == hashlib.sha256(payload).digest()

    # List shows it
    r = client.get(f"/clouds/{cloud_id}/files")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    rows = body["items"]
    assert len(rows) == 1
    assert rows[0]["id"] == file_id

    # Delete it (soft delete + Telegram delete)
    r = client.delete(f"/files/{file_id}")
    assert r.status_code == 204
    assert captured["deletions"] == [(-1_001_111_111_111, 777)]

    # Now hidden from listing
    r = client.get(f"/clouds/{cloud_id}/files")
    assert r.json()["items"] == []


def test_upload_rejects_oversized_file(
    authed_app_with_cloud: tuple[TestClient, int],
) -> None:
    client, cloud_id = authed_app_with_cloud
    big = b"X" * 20_000  # > LC_MAX_FILE_BYTES=10_000
    r = client.post(
        f"/clouds/{cloud_id}/files",
        files={"file": ("huge.bin", big, "application/octet-stream")},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["reason"] == "file_too_large"


def test_upload_unknown_cloud_returns_404(
    authed_app_with_cloud: tuple[TestClient, int],
) -> None:
    client, _ = authed_app_with_cloud
    r = client.post(
        "/clouds/9999/files",
        files={"file": ("x.txt", b"hi", "text/plain")},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "cloud_not_found"


def test_download_streams_chunks(
    authed_app_with_cloud: tuple[TestClient, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, cloud_id = authed_app_with_cloud

    blob = b"chunk1-" + b"chunk2-" + b"chunk3"

    async def fake_upload(
        cli: Any, *, chat_id: int, file_path: Path,
        original_name: str, sha256_digest: bytes, signing_key: Any,
    ) -> UploadResult:
        return UploadResult(
            message_id=42,
            caption="LC1:fake",
            uploaded_at_unix=1700000000,
            signature=b"\x00" * 64,
        )

    async def fake_iter_download(
        cli: Any, *, chat_id: int, message_id: int, chunk_size: int = 0
    ) -> AsyncIterator[bytes]:
        for c in [b"chunk1-", b"chunk2-", b"chunk3"]:
            yield c

    import lcloud.api.files as files_mod

    monkeypatch.setattr(files_mod, "upload_file_to_cloud", fake_upload)
    monkeypatch.setattr(files_mod, "iter_download_file", fake_iter_download)

    r = client.post(
        f"/clouds/{cloud_id}/files",
        files={"file": ("doc.txt", blob, "text/plain")},
    )
    assert r.status_code == 201
    file_id = r.json()["id"]

    r = client.get(f"/files/{file_id}/download")
    assert r.status_code == 200
    assert r.content == blob
    assert r.headers["content-type"].startswith("text/plain")
    assert "doc.txt" in r.headers["content-disposition"]
    assert r.headers["content-length"] == str(len(blob))


def test_rename_file_updates_db_row(
    authed_app_with_cloud: tuple[TestClient, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, cloud_id = authed_app_with_cloud

    async def fake_upload(
        cli: Any, *, chat_id: int, file_path: Path,
        original_name: str, sha256_digest: bytes, signing_key: Any,
    ) -> UploadResult:
        return UploadResult(
            message_id=1, caption="LC1:fake", uploaded_at_unix=1, signature=b"\x00" * 64,
        )

    import lcloud.api.files as files_mod

    monkeypatch.setattr(files_mod, "upload_file_to_cloud", fake_upload)

    r = client.post(
        f"/clouds/{cloud_id}/files",
        files={"file": ("draft.txt", b"hi", "text/plain")},
    )
    fid = r.json()["id"]

    r = client.patch(f"/files/{fid}", json={"name": "final-report.pdf"})
    assert r.status_code == 200
    assert r.json()["name"] == "final-report.pdf"

    r = client.get(f"/clouds/{cloud_id}/files")
    assert r.json()["items"][0]["name"] == "final-report.pdf"


def test_rename_unknown_file_returns_404(
    authed_app_with_cloud: tuple[TestClient, int],
) -> None:
    client, _ = authed_app_with_cloud
    r = client.patch("/files/999", json={"name": "x"})
    assert r.status_code == 404


def test_rename_validates_name(
    authed_app_with_cloud: tuple[TestClient, int],
) -> None:
    client, _ = authed_app_with_cloud
    r = client.patch("/files/1", json={"name": ""})
    assert r.status_code == 422
    r = client.patch("/files/1", json={"name": "x" * 1000})
    assert r.status_code == 422


def test_pagination_limit_and_offset(
    authed_app_with_cloud: tuple[TestClient, int],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, cloud_id = authed_app_with_cloud

    counter = {"n": 0}

    async def fake_upload(
        cli: Any, *, chat_id: int, file_path: Path,
        original_name: str, sha256_digest: bytes, signing_key: Any,
    ) -> UploadResult:
        counter["n"] += 1
        return UploadResult(
            message_id=counter["n"], caption="LC1:fake",
            uploaded_at_unix=1700000000 + counter["n"], signature=b"\x00" * 64,
        )

    import lcloud.api.files as files_mod

    monkeypatch.setattr(files_mod, "upload_file_to_cloud", fake_upload)

    for i in range(7):
        client.post(
            f"/clouds/{cloud_id}/files",
            files={"file": (f"file-{i}.txt", b"x", "text/plain")},
        )

    r = client.get(f"/clouds/{cloud_id}/files?limit=3&offset=0")
    body = r.json()
    assert body["total"] == 7
    assert body["limit"] == 3
    assert body["offset"] == 0
    assert len(body["items"]) == 3

    r = client.get(f"/clouds/{cloud_id}/files?limit=3&offset=3")
    body = r.json()
    assert len(body["items"]) == 3
    assert body["offset"] == 3

    r = client.get(f"/clouds/{cloud_id}/files?limit=3&offset=6")
    body = r.json()
    assert len(body["items"]) == 1


def test_thumb_redirects_when_no_thumbs(
    authed_app_with_cloud: tuple[TestClient, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, cloud_id = authed_app_with_cloud

    async def fake_upload(
        cli: Any, *, chat_id: int, file_path: Path,
        original_name: str, sha256_digest: bytes, signing_key: Any,
    ) -> UploadResult:
        return UploadResult(
            message_id=42, caption="LC1:fake",
            uploaded_at_unix=1700000000, signature=b"\x00" * 64,
        )

    import lcloud.api.files as files_mod

    monkeypatch.setattr(files_mod, "upload_file_to_cloud", fake_upload)

    r = client.post(
        f"/clouds/{cloud_id}/files",
        files={"file": ("a.bin", b"hi", "application/octet-stream")},
    )
    fid = r.json()["id"]

    # high → always redirect to /download
    r = client.get(f"/files/{fid}/thumb?size=high", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith(f"/files/{fid}/download")


def test_unauthenticated_routes_return_401(
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
            assert client.get("/clouds/1/files").status_code == 401
            assert client.get("/files/1/download").status_code == 401
            assert client.delete("/files/1").status_code == 401
    finally:
        set_userbot_manager(None)
        get_settings.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None


# Suppress unused-import warning; needed for test_userbot.FakeTelegramClient
_ = (FakeTGClient,)
