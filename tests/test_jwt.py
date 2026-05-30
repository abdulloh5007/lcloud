"""Tests for JWT helpers + secret management."""

from __future__ import annotations

import time
from pathlib import Path

import jwt as pyjwt
import pytest

from lcloud.auth.jwt_utils import (
    JWT_ALG,
    decode_admin_token,
    ensure_jwt_secret,
    issue_admin_token,
    jwt_secret_path,
)
from lcloud.config import Settings


@pytest.fixture
def isolated_settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, lc_data_dir=tmp_path)


def test_jwt_secret_created_with_correct_perms(
    isolated_settings: Settings,
) -> None:
    secret = ensure_jwt_secret(isolated_settings)
    assert len(secret) == 32
    p = jwt_secret_path(isolated_settings)
    assert p.exists()
    mode = p.stat().st_mode & 0o777
    assert mode == 0o600


def test_jwt_secret_idempotent(isolated_settings: Settings) -> None:
    s1 = ensure_jwt_secret(isolated_settings)
    s2 = ensure_jwt_secret(isolated_settings)
    assert s1 == s2


def test_jwt_roundtrip(isolated_settings: Settings) -> None:
    token = issue_admin_token(
        owner_id=1, auth_epoch=1, settings=isolated_settings
    )
    payload = decode_admin_token(token, settings=isolated_settings)
    assert payload["sub"] == "admin"
    assert payload["owner_id"] == 1
    assert payload["ae"] == 1
    assert "jti" in payload
    assert payload["exp"] > payload["iat"]


def test_jwt_expired_raises(isolated_settings: Settings) -> None:
    # Set iat way in the past so exp < now
    long_ago = int(time.time()) - isolated_settings.lc_session_ttl_seconds - 60
    token = issue_admin_token(
        owner_id=1, auth_epoch=1, settings=isolated_settings, now=long_ago
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_admin_token(token, settings=isolated_settings)


def test_jwt_wrong_secret_fails(isolated_settings: Settings, tmp_path: Path) -> None:
    token = issue_admin_token(
        owner_id=1, auth_epoch=1, settings=isolated_settings
    )
    other_settings = Settings(_env_file=None, lc_data_dir=tmp_path / "other")
    # Force a different secret
    ensure_jwt_secret(other_settings)
    with pytest.raises(pyjwt.InvalidSignatureError):
        decode_admin_token(token, settings=other_settings)


def test_jwt_alg_pinned() -> None:
    assert JWT_ALG == "HS256"
