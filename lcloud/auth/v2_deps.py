"""V2 auth dependency: cookie session OR Bearer API key.

Used by all V2 data endpoints that need to know "who is the calling user?".
Tries cookie first (browser sessions), then `Authorization: Bearer <api_key>`
(programmatic clients).

Returns a fully-loaded `User` ORM row. Raises 401/403 on failure.

Note: this is the V2 user-identity dep; for V1 admin (cookie-only)
endpoints use `lcloud.auth.deps.require_admin`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

import jwt as pyjwt
import sqlalchemy as sa
from fastapi import Cookie, Depends, Header, HTTPException

from lcloud.auth import api_keys as ak
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import ApiKey, User

logger = logging.getLogger(__name__)


async def _user_from_cookie(token: str) -> User | None:
    # Lazy import to avoid circular: lcloud.api.auth_v2 → ... → lcloud.auth
    from lcloud.api.auth_v2 import decode_user_session

    try:
        payload = decode_user_session(token)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, detail={"reason": "session_expired"}) from None
    except pyjwt.PyJWTError:
        raise HTTPException(401, detail={"reason": "invalid_session"}) from None

    sm = get_sessionmaker()
    async with sm() as sess:
        user = (
            await sess.execute(
                sa.select(User).where(User.id == int(payload["user_id"]))
            )
        ).scalar_one_or_none()
    return user


async def _user_from_api_key(raw: str) -> User | None:
    """Look up a user by API key. Updates last_used_at on success."""
    prefix = ak.extract_prefix(raw)
    if not prefix:
        return None
    sm = get_sessionmaker()
    async with sm() as sess:
        rows = (
            await sess.execute(
                sa.select(ApiKey).where(
                    ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None)
                )
            )
        ).scalars().all()
        for row in rows:
            if ak.verify(raw, row.hash):
                row.last_used_at = datetime.now(UTC)
                user = (
                    await sess.execute(
                        sa.select(User).where(User.id == row.user_id)
                    )
                ).scalar_one_or_none()
                await sess.commit()
                return user
    return None


async def get_current_user(
    lc_user_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """FastAPI dep — returns the authenticated User or raises 401/403."""
    user: User | None = None

    if lc_user_session:
        user = await _user_from_cookie(lc_user_session)

    if user is None and authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and ak.looks_like_api_key(token):
            user = await _user_from_api_key(token)

    if user is None:
        raise HTTPException(
            401,
            detail={"reason": "no_credentials"},
            headers={"WWW-Authenticate": 'Bearer realm="LCloud"'},
        )

    if user.suspended_at is not None:
        raise HTTPException(403, detail={"reason": "suspended"})

    return user


async def get_optional_current_user(
    lc_user_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    """Return authenticated user when credentials are present, otherwise None."""
    if not lc_user_session and not authorization:
        return None

    user: User | None = None

    if lc_user_session:
        user = await _user_from_cookie(lc_user_session)

    if user is None and authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and ak.looks_like_api_key(token):
            user = await _user_from_api_key(token)

    if user is None:
        raise HTTPException(
            401,
            detail={"reason": "invalid_credentials"},
            headers={"WWW-Authenticate": 'Bearer realm="LCloud"'},
        )

    if user.suspended_at is not None:
        raise HTTPException(403, detail={"reason": "suspended"})

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalCurrentUser = Annotated[User | None, Depends(get_optional_current_user)]


def require_user_admin(user: CurrentUser) -> User:
    """V2-side admin check (separate from V1 cookie-only `require_admin`)."""
    if user.role != "admin":
        raise HTTPException(403, detail={"reason": "admin_only"})
    return user


CurrentUserAdmin = Annotated[User, Depends(require_user_admin)]


__all__ = [
    "CurrentUser",
    "CurrentUserAdmin",
    "OptionalCurrentUser",
    "get_current_user",
    "get_optional_current_user",
    "require_user_admin",
]
