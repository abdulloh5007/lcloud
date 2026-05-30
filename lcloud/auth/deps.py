"""FastAPI dependencies for admin auth (cookie + epoch validation)."""

from __future__ import annotations

import jwt as pyjwt
from fastapi import Cookie, HTTPException
from sqlalchemy import select

from lcloud.auth.cookies import COOKIE_NAME
from lcloud.auth.jwt_utils import decode_admin_token
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import AuthState


async def require_admin(lc_session: str | None = Cookie(default=None)) -> int:
    """Return owner_id if the cookie is valid; raise 401 otherwise."""
    if not lc_session:
        raise HTTPException(401, detail={"reason": "no_session"})
    try:
        payload = decode_admin_token(lc_session)
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(401, detail={"reason": "expired"}) from exc
    except pyjwt.PyJWTError as exc:
        raise HTTPException(401, detail={"reason": "invalid_session"}) from exc

    if payload.get("sub") != "admin":
        raise HTTPException(401, detail={"reason": "wrong_subject"})

    try:
        owner_id = int(payload["owner_id"])
        cookie_epoch = int(payload["ae"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, detail={"reason": "malformed_claims"}) from exc

    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            select(AuthState.epoch).where(AuthState.owner_id == owner_id)
        )
        current_epoch = result.scalar_one_or_none()
    if current_epoch is None or cookie_epoch != current_epoch:
        raise HTTPException(401, detail={"reason": "epoch_mismatch"})

    return owner_id


# Ensure unused-name doesn't accidentally drop the import (cookie name needed elsewhere)
__all__ = ["COOKIE_NAME", "require_admin"]
