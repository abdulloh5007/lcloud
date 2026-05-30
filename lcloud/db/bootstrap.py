"""Bootstrap: run Alembic migrations + ensure admin owner row.

Called from `lcloud.main.lifespan` on every startup. Idempotent.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import sqlalchemy as sa

from alembic import command
from alembic.config import Config as AlembicConfig
from lcloud.config import Settings, get_settings
from lcloud.db.base import get_sessionmaker, init_engine
from lcloud.db.models import AuthState, Owner

logger = logging.getLogger(__name__)


def _alembic_config(settings: Settings) -> AlembicConfig:
    cfg = AlembicConfig(str(settings.project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(settings.project_root / "alembic"))
    # env.py reads this and rewrites async->sync; takes precedence over .ini
    cfg.attributes["lc_settings"] = settings
    return cfg


def run_migrations_sync(settings: Settings | None = None) -> None:
    """Synchronously upgrade the DB to head. Safe to call repeatedly."""
    s = settings or get_settings()
    s.ensure_runtime_dirs()
    # Make sure the parent dir for the sqlite file exists
    sync_url = s.lc_db_url.replace("sqlite+aiosqlite", "sqlite", 1)
    if sync_url.startswith("sqlite:///"):
        db_path = Path(sync_url[len("sqlite:///") :])
        db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = _alembic_config(s)
    logger.info("running alembic upgrade head (url=%s)", sync_url)
    command.upgrade(cfg, "head")


async def run_migrations(settings: Settings | None = None) -> None:
    """Async wrapper for `run_migrations_sync` (offloads to a thread)."""
    await asyncio.to_thread(run_migrations_sync, settings)


async def ensure_admin_owner(pubkey: bytes, label: str = "admin") -> int:
    """Ensure an `owners` row exists for the given pubkey. Returns its id."""
    init_engine()
    sm = get_sessionmaker()
    async with sm() as sess:
        existing = await sess.execute(
            sa.select(Owner).where(Owner.pubkey == pubkey)
        )
        owner = existing.scalar_one_or_none()
        if owner is not None:
            return owner.id

        owner = Owner(pubkey=pubkey, label=label, role="admin")
        sess.add(owner)
        await sess.commit()
        await sess.refresh(owner)

        # Seed auth_state.epoch=1 for the new owner
        sess.add(AuthState(owner_id=owner.id, epoch=1))
        await sess.commit()
        logger.info("created admin owner row id=%s", owner.id)
        return owner.id
