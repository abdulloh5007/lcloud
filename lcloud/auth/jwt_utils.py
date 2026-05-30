"""HS256 JWT helpers for the admin web cookie."""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from pathlib import Path
from typing import Any

import jwt

from lcloud.config import Settings, get_settings

JWT_ALG = "HS256"
JWT_SECRET_FILENAME = "jwt.secret"


def _atomic_write_secret(path: Path, data: bytes) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    success = False
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        success = True
    finally:
        if not success:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()


def jwt_secret_path(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return s.keys_dir / JWT_SECRET_FILENAME


def ensure_jwt_secret(settings: Settings | None = None) -> bytes:
    """Load (or generate, on first run) the 32-byte HS256 secret."""
    s = settings or get_settings()
    s.ensure_runtime_dirs()
    p = jwt_secret_path(s)
    if p.exists():
        return p.read_bytes()
    secret = os.urandom(32)
    _atomic_write_secret(p, secret)
    return secret


def issue_admin_token(
    *,
    owner_id: int,
    auth_epoch: int,
    settings: Settings | None = None,
    now: int | None = None,
) -> str:
    s = settings or get_settings()
    secret = ensure_jwt_secret(s)
    iat = now if now is not None else int(time.time())
    exp = iat + s.lc_session_ttl_seconds
    payload: dict[str, Any] = {
        "sub": "admin",
        "kind": "session",
        "owner_id": owner_id,
        "ae": auth_epoch,
        "iat": iat,
        "exp": exp,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALG)


def issue_magic_token(
    *,
    owner_id: int,
    auth_epoch: int,
    settings: Settings | None = None,
    now: int | None = None,
) -> str:
    """Single-use, short-TTL admin login link (Saved-Messages `/admin`).

    Distinguished from the session cookie by `kind: "magic"`. The `jti`
    is recorded in `used_tokens` on first redemption to prevent replay.
    """
    s = settings or get_settings()
    secret = ensure_jwt_secret(s)
    iat = now if now is not None else int(time.time())
    exp = iat + s.lc_magic_link_ttl_seconds
    payload: dict[str, Any] = {
        "sub": "admin",
        "kind": "magic",
        "owner_id": owner_id,
        "ae": auth_epoch,
        "iat": iat,
        "exp": exp,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALG)


def decode_admin_token(
    token: str, settings: Settings | None = None
) -> dict[str, Any]:
    s = settings or get_settings()
    secret = ensure_jwt_secret(s)
    payload: dict[str, Any] = jwt.decode(token, secret, algorithms=[JWT_ALG])
    return payload
