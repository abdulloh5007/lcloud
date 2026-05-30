"""Alembic environment.

Resolves the DB URL from `lcloud.config.Settings` so there is one source of
truth (the `.env`). The async URL `sqlite+aiosqlite:///...` is rewritten to
sync `sqlite:///...` because Alembic uses synchronous DBAPI internally.

Programmatic callers (e.g. `lcloud.db.bootstrap.run_migrations_sync`) can
inject an explicit `Settings` instance via `cfg.attributes["lc_settings"]`
to override the global cached settings (e.g. for tests pointing at a tmp DB).
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from lcloud.config import get_settings

# Importing `lcloud.db.base` triggers `lcloud.db.__init__`, which side-effect-
# imports all model modules so they get registered on `Base.metadata`.
from lcloud.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve settings: caller-provided wins, otherwise fall back to the cached global
_settings = config.attributes.get("lc_settings") or get_settings()
_sync_url = _settings.lc_db_url.replace("sqlite+aiosqlite", "sqlite", 1)
config.set_main_option("sqlalchemy.url", _sync_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
