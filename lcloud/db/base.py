"""SQLAlchemy async engine + session factory + declarative Base."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from lcloud.config import Settings, get_settings


class Base(DeclarativeBase):
    """Common base class for all ORM models."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _enable_sqlite_pragmas(dbapi_conn: Any, _conn_rec: Any) -> None:
    """Enable WAL + foreign keys + a sane busy timeout on every new connection."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def init_engine(settings: Settings | None = None) -> AsyncEngine:
    """Create the singleton async engine (idempotent)."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine
    s = settings or get_settings()
    _engine = create_async_engine(s.lc_db_url, future=True)
    event.listen(_engine.sync_engine, "connect", _enable_sqlite_pragmas)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        return init_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency-style async generator yielding a session."""
    sm = get_sessionmaker()
    async with sm() as sess:
        yield sess


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
