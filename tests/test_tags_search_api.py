"""Tests for /tags CRUD + /files/{id}/tags assignment + /search."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from lcloud.config import Settings
from lcloud.db.bootstrap import run_migrations_sync
from lcloud.db.models import Cloud, File, Owner
from lcloud.userbot.client import UserbotManager
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

    from lcloud.config import get_settings
    from lcloud.db import base as base_mod

    get_settings.cache_clear()
    base_mod._engine = None
    base_mod._sessionmaker = None
    return get_settings()


@pytest.fixture
def authed_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, int]]:
    """Authenticated TestClient with one cloud row already created."""
    settings = _bootstrap_isolated_env(tmp_path, monkeypatch)

    fake = FakeTelegramClient(me_id=42)
    mgr = UserbotManager(settings)
    monkeypatch.setattr(mgr, "_build_client", lambda: fake)  # type: ignore[arg-type]

    from lcloud.api.auth import get_login_rate_limiter
    from lcloud.userbot.client import set_userbot_manager

    set_userbot_manager(mgr)
    get_login_rate_limiter().reset()

    async def fake_create_cloud(
        client: Any, *, name: str, signing_key: Any
    ) -> tuple[int, str, Any]:
        return -1_001_111_111_111, "LCLOUD1:fake", object()

    import lcloud.api.clouds as clouds_mod

    monkeypatch.setattr(clouds_mod, "create_cloud_chat", fake_create_cloud)

    from lcloud.config import get_settings as _gs
    from lcloud.db import base as base_mod
    from lcloud.main import create_app

    app = create_app()
    try:
        with TestClient(app) as client:
            client.post("/auth/telegram/start", json={"phone": "+1234567"})
            client.post("/auth/telegram/code", json={"code": "12345"})
            r = client.post("/clouds", json={"name": "Photos"})
            cloud_id = r.json()["id"]
            yield client, cloud_id
    finally:
        set_userbot_manager(None)
        _gs.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None


# ------------------------------------------------------------------ /tags CRUD


def test_tags_crud_lifecycle(authed_app: tuple[TestClient, int]) -> None:
    client, _ = authed_app

    # initially empty
    assert client.get("/tags").json() == []

    # create
    r = client.post(
        "/tags",
        json={
            "name": "Important",
            "color": "#ff0033",
            "icon": "star",
            "bg_color": "#fff",
        },
    )
    assert r.status_code == 201, r.text
    tag = r.json()
    assert tag["name"] == "Important"
    tag_id = tag["id"]

    # duplicate name → 409
    r = client.post(
        "/tags",
        json={"name": "Important", "color": "red", "icon": "x", "bg_color": "white"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "tag_name_exists"

    # list
    rows = client.get("/tags").json()
    assert len(rows) == 1 and rows[0]["id"] == tag_id

    # patch
    r = client.patch(f"/tags/{tag_id}", json={"color": "#000", "name": "Critical"})
    assert r.status_code == 200
    assert r.json()["name"] == "Critical"
    assert r.json()["color"] == "#000"

    # patch with invalid colour
    r = client.patch(f"/tags/{tag_id}", json={"color": "not-a-color!"})
    assert r.status_code == 422

    # delete
    r = client.delete(f"/tags/{tag_id}")
    assert r.status_code == 204
    assert client.get("/tags").json() == []


def test_create_tag_validates_color(authed_app: tuple[TestClient, int]) -> None:
    client, _ = authed_app
    r = client.post(
        "/tags",
        json={"name": "X", "color": "###", "icon": "y", "bg_color": "white"},
    )
    assert r.status_code == 422


def test_unknown_tag_returns_404(authed_app: tuple[TestClient, int]) -> None:
    client, _ = authed_app
    assert client.patch("/tags/999", json={"name": "X"}).status_code == 404
    assert client.delete("/tags/999").status_code == 404


# ------------------------------------------------------------------ /files/{id}/tags

def _seed_file(tmp_path: Path, cloud_id: int, name: str = "doc.txt") -> int:
    """Insert a File row directly into the DB for a given cloud."""
    from sqlalchemy import create_engine

    sync_url = f"sqlite:///{tmp_path / 'lcloud.db'}"
    eng = create_engine(sync_url)
    with eng.begin() as conn:
        # Find owner_id via the cloud row
        owner_id = conn.execute(
            sa.text("SELECT owner_id FROM clouds WHERE id=:c"),
            {"c": cloud_id},
        ).scalar_one()
        digest = hashlib.sha256(name.encode()).digest()
        result = conn.execute(
            sa.text(
                "INSERT INTO files (cloud_id, message_id, owner_id, "
                "original_name, mime, size_bytes, sha256, signature) "
                "VALUES (:cl, :mid, :ow, :n, 'text/plain', 100, :h, :s) "
                "RETURNING id"
            ),
            {
                "cl": cloud_id,
                "mid": abs(hash(name)) % 10000 + 1,
                "ow": owner_id,
                "n": name,
                "h": digest,
                "s": b"\x00" * 64,
            },
        )
        file_id = int(result.scalar_one())
    eng.dispose()
    return file_id


def test_file_tags_assignment(
    authed_app: tuple[TestClient, int], tmp_path: Path
) -> None:
    client, cloud_id = authed_app
    file_id = _seed_file(tmp_path, cloud_id, name="report.pdf")

    # Create two tags
    t1 = client.post(
        "/tags",
        json={"name": "Important", "color": "red", "icon": "star", "bg_color": "#fff"},
    ).json()
    t2 = client.post(
        "/tags",
        json={"name": "Work", "color": "blue", "icon": "briefcase", "bg_color": "#fff"},
    ).json()

    # Assign both
    r = client.put(
        f"/files/{file_id}/tags", json={"tag_ids": [t1["id"], t2["id"]]}
    )
    assert r.status_code == 200
    assert sorted(r.json()["tag_ids"]) == sorted([t1["id"], t2["id"]])

    # GET reflects assignment
    rows = client.get(f"/files/{file_id}/tags").json()
    assert sorted(r["name"] for r in rows) == ["Important", "Work"]

    # Replace with subset
    r = client.put(f"/files/{file_id}/tags", json={"tag_ids": [t1["id"]]})
    assert r.status_code == 200
    rows = client.get(f"/files/{file_id}/tags").json()
    assert [r["name"] for r in rows] == ["Important"]

    # Replace with []
    r = client.put(f"/files/{file_id}/tags", json={"tag_ids": []})
    assert r.status_code == 200
    assert client.get(f"/files/{file_id}/tags").json() == []


def test_file_tags_unknown_tag_id(
    authed_app: tuple[TestClient, int], tmp_path: Path
) -> None:
    client, cloud_id = authed_app
    file_id = _seed_file(tmp_path, cloud_id)
    r = client.put(f"/files/{file_id}/tags", json={"tag_ids": [99999]})
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "unknown_tag_ids"


def test_file_tags_unknown_file(
    authed_app: tuple[TestClient, int],
) -> None:
    client, _ = authed_app
    r = client.put("/files/99999/tags", json={"tag_ids": []})
    assert r.status_code == 404


def test_delete_tag_cascades_file_tags(
    authed_app: tuple[TestClient, int], tmp_path: Path
) -> None:
    client, cloud_id = authed_app
    file_id = _seed_file(tmp_path, cloud_id, name="x.bin")
    t = client.post(
        "/tags",
        json={"name": "Tmp", "color": "gray", "icon": "x", "bg_color": "#eee"},
    ).json()
    client.put(f"/files/{file_id}/tags", json={"tag_ids": [t["id"]]})
    assert len(client.get(f"/files/{file_id}/tags").json()) == 1

    client.delete(f"/tags/{t['id']}")  # should cascade
    assert client.get(f"/files/{file_id}/tags").json() == []


# ------------------------------------------------------------------ /search


def _seed_files_and_tags(
    tmp_path: Path, cloud_id: int, files_with_tags: list[tuple[str, list[int]]]
) -> dict[str, int]:
    """Bulk insert files + assign tag ids; returns name → file_id."""
    from sqlalchemy import create_engine

    sync_url = f"sqlite:///{tmp_path / 'lcloud.db'}"
    eng = create_engine(sync_url)
    name_to_id: dict[str, int] = {}
    with eng.begin() as conn:
        owner_id = conn.execute(
            sa.text("SELECT owner_id FROM clouds WHERE id=:c"),
            {"c": cloud_id},
        ).scalar_one()
        for i, (name, tag_ids) in enumerate(files_with_tags):
            digest = hashlib.sha256(name.encode()).digest()
            res = conn.execute(
                sa.text(
                    "INSERT INTO files (cloud_id, message_id, owner_id, "
                    "original_name, mime, size_bytes, sha256, signature) "
                    "VALUES (:cl, :mid, :ow, :n, 'text/plain', 100, :h, :s) "
                    "RETURNING id"
                ),
                {
                    "cl": cloud_id,
                    "mid": 1000 + i,
                    "ow": owner_id,
                    "n": name,
                    "h": digest,
                    "s": b"\x00" * 64,
                },
            )
            fid = int(res.scalar_one())
            name_to_id[name] = fid
            for tid in tag_ids:
                conn.execute(
                    sa.text(
                        "INSERT INTO file_tags (file_id, tag_id) VALUES (:f, :t)"
                    ),
                    {"f": fid, "t": tid},
                )
    eng.dispose()
    return name_to_id


def test_search_by_name_uses_fts(
    authed_app: tuple[TestClient, int], tmp_path: Path
) -> None:
    client, cloud_id = authed_app
    _seed_files_and_tags(
        tmp_path,
        cloud_id,
        [("vacation photos.jpg", []), ("yearly report.pdf", []), ("notes.txt", [])],
    )

    r = client.get("/search", params={"q": "report"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "yearly report.pdf"

    r = client.get("/search", params={"q": "phot"})  # prefix matches "photos"
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "vacation photos.jpg"


def test_search_combines_tags_intersection(
    authed_app: tuple[TestClient, int], tmp_path: Path
) -> None:
    client, cloud_id = authed_app
    t_imp = client.post(
        "/tags",
        json={"name": "Important", "color": "red", "icon": "x", "bg_color": "#fff"},
    ).json()
    t_work = client.post(
        "/tags",
        json={"name": "Work", "color": "blue", "icon": "y", "bg_color": "#fff"},
    ).json()

    seeded = _seed_files_and_tags(
        tmp_path,
        cloud_id,
        [
            ("a-important.txt", [t_imp["id"]]),
            ("b-work.txt", [t_work["id"]]),
            ("c-both.txt", [t_imp["id"], t_work["id"]]),
            ("d-neither.txt", []),
        ],
    )

    # Filter by single tag
    items = client.get("/search", params={"tag": [t_imp["id"]]}).json()["items"]
    names = sorted(i["name"] for i in items)
    assert names == ["a-important.txt", "c-both.txt"]

    # Intersection (both tags)
    items = client.get(
        "/search", params={"tag": [t_imp["id"], t_work["id"]]}
    ).json()["items"]
    names = [i["name"] for i in items]
    assert names == ["c-both.txt"]

    # No filter: returns all (4)
    items = client.get("/search").json()["items"]
    assert len(items) == 4
    _ = seeded  # silence unused


def test_search_combines_q_and_tags(
    authed_app: tuple[TestClient, int], tmp_path: Path
) -> None:
    client, cloud_id = authed_app
    t = client.post(
        "/tags",
        json={"name": "Photos", "color": "red", "icon": "i", "bg_color": "#fff"},
    ).json()
    _seed_files_and_tags(
        tmp_path,
        cloud_id,
        [
            ("vacation photos.jpg", [t["id"]]),
            ("vacation video.mp4", [t["id"]]),
            ("photos report.pdf", []),  # has "photos" in name but not the tag
        ],
    )

    items = client.get(
        "/search", params={"q": "vacation", "tag": [t["id"]]}
    ).json()["items"]
    names = sorted(i["name"] for i in items)
    assert names == ["vacation photos.jpg", "vacation video.mp4"]


def test_search_unauthenticated_returns_401(
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
            assert client.get("/search").status_code == 401
            assert client.get("/tags").status_code == 401
    finally:
        set_userbot_manager(None)
        get_settings.cache_clear()
        base_mod._engine = None
        base_mod._sessionmaker = None


# Suppress unused-import (helper imports for sub-tests)
_ = (Owner, File, Cloud, run_migrations_sync)
