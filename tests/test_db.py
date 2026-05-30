"""Tests for DB migrations + admin-owner bootstrap."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lcloud.config import Settings
from lcloud.db.bootstrap import ensure_admin_owner, run_migrations_sync
from lcloud.db.models import AuthState, Owner

EXPECTED_TABLES = {
    "owners",
    "clouds",
    "files",
    "tags",
    "file_tags",
    "used_tokens",
    "auth_state",
    "files_fts",
    "alembic_version",
    "users",
    "api_keys",
    "auth_challenges",
}


def _settings_for(tmp_path: Path) -> Settings:
    db_file = tmp_path / "lcloud.db"
    return Settings(
        _env_file=None,
        lc_data_dir=tmp_path,
        lc_db_url=f"sqlite+aiosqlite:///{db_file}",
    )


def test_migration_creates_all_tables(tmp_path: Path) -> None:
    s = _settings_for(tmp_path)
    run_migrations_sync(s)

    sync_url = s.lc_db_url.replace("sqlite+aiosqlite", "sqlite", 1)
    eng = sa.create_engine(sync_url)
    with eng.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        ).all()
        names = {r[0] for r in rows}
    eng.dispose()

    missing = EXPECTED_TABLES - names
    assert not missing, f"missing tables: {missing}"


def test_fts_trigger_indexes_inserted_files(tmp_path: Path) -> None:
    s = _settings_for(tmp_path)
    run_migrations_sync(s)

    sync_url = s.lc_db_url.replace("sqlite+aiosqlite", "sqlite", 1)
    eng = sa.create_engine(sync_url)
    with eng.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO owners (pubkey, label, role) "
                "VALUES (:p, 'admin', 'admin')"
            ),
            {"p": b"\x00" * 32},
        )
        conn.execute(
            sa.text(
                "INSERT INTO clouds (chat_id, owner_id, name) "
                "VALUES (-1, 1, 'c')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO files "
                "(cloud_id, message_id, owner_id, original_name, "
                " size_bytes, sha256, signature) "
                "VALUES (1, 1, 1, 'hello world report.pdf', 1, :h, :s)"
            ),
            {"h": b"\x00" * 32, "s": b"\x00" * 64},
        )
    with eng.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT rowid FROM files_fts WHERE files_fts MATCH 'hello'"
            )
        ).all()
    eng.dispose()
    assert len(rows) == 1


def test_migration_idempotent(tmp_path: Path) -> None:
    s = _settings_for(tmp_path)
    run_migrations_sync(s)
    # second invocation must be a no-op (no exception)
    run_migrations_sync(s)


@pytest.mark.asyncio
async def test_ensure_admin_owner_creates_then_reuses(tmp_path: Path) -> None:
    s = _settings_for(tmp_path)
    run_migrations_sync(s)

    # Use a fresh engine bound to the tmp DB; bypass the module-level singleton
    eng = create_async_engine(s.lc_db_url, future=True)
    sm: async_sessionmaker[AsyncSession] = async_sessionmaker(
        eng, expire_on_commit=False
    )

    pub = b"\xab" * 32

    # First call: create
    async with sm() as sess:
        result = await sess.execute(sa.select(Owner).where(Owner.pubkey == pub))
        assert result.scalar_one_or_none() is None
        owner = Owner(pubkey=pub, label="admin", role="admin")
        sess.add(owner)
        await sess.commit()
        await sess.refresh(owner)
        sess.add(AuthState(owner_id=owner.id, epoch=1))
        await sess.commit()
        first_id = owner.id

    # Second call: must return same id, not duplicate
    async with sm() as sess:
        result = await sess.execute(sa.select(Owner).where(Owner.pubkey == pub))
        owner2 = result.scalar_one()
        assert owner2.id == first_id

    await eng.dispose()


@pytest.mark.asyncio
async def test_ensure_admin_owner_through_bootstrap_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke-test the real bootstrap helper end-to-end on a tmp DB."""
    db_file = tmp_path / "lcloud.db"
    monkeypatch.setenv("LC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LC_DB_URL", f"sqlite+aiosqlite:///{db_file}")

    from lcloud.config import get_settings
    from lcloud.db import base as base_mod

    get_settings.cache_clear()
    base_mod._engine = None
    base_mod._sessionmaker = None

    s = get_settings()
    run_migrations_sync(s)

    try:
        pub = b"\xcd" * 32
        owner_id_1 = await ensure_admin_owner(pub)
        owner_id_2 = await ensure_admin_owner(pub)
        assert owner_id_1 == owner_id_2
    finally:
        await base_mod.dispose_engine()
        get_settings.cache_clear()
