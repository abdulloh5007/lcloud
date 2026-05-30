"""Magic-link login endpoint: `GET /admin?token=<jwt>`.

Single-use admin login from the link the userbot posts to Saved Messages.
Validates the JWT, marks the jti as used, sets `lc_session` cookie,
redirects to `/`. On any validation failure → 401 JSON.
"""

from __future__ import annotations

import logging

import jwt as pyjwt
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import RedirectResponse

from lcloud.auth.cookies import set_session_cookie
from lcloud.auth.jwt_utils import decode_admin_token, issue_admin_token
from lcloud.config import get_settings
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import AuthState, Owner, UsedToken

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


@router.get("/admin")
async def admin_magic_login(
    token: str = Query(..., min_length=20, max_length=4096),
) -> Response:
    settings = get_settings()
    try:
        payload = decode_admin_token(token, settings=settings)
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(401, detail={"reason": "expired"}) from exc
    except pyjwt.PyJWTError as exc:
        raise HTTPException(401, detail={"reason": "invalid_token"}) from exc

    if payload.get("sub") != "admin":
        raise HTTPException(401, detail={"reason": "wrong_subject"})
    if payload.get("kind") != "magic":
        # Defends against a session JWT being pasted into ?token=
        raise HTTPException(401, detail={"reason": "wrong_kind"})

    try:
        jti = str(payload["jti"])
        owner_id = int(payload["owner_id"])
        token_epoch = int(payload["ae"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, detail={"reason": "malformed_claims"}) from exc

    sm = get_sessionmaker()
    async with sm() as sess:
        # Replay protection: jti must be unused
        already = await sess.execute(
            sa.select(UsedToken).where(UsedToken.jti == jti)
        )
        if already.scalar_one_or_none() is not None:
            raise HTTPException(401, detail={"reason": "replay"})

        # Owner must still be the admin and not revoked
        owner = (
            await sess.execute(
                sa.select(Owner).where(
                    Owner.id == owner_id, Owner.role == "admin"
                )
            )
        ).scalar_one_or_none()
        if owner is None:
            raise HTTPException(401, detail={"reason": "admin_owner_missing"})

        epoch = (
            await sess.execute(
                sa.select(AuthState.epoch).where(AuthState.owner_id == owner_id)
            )
        ).scalar_one_or_none()
        current_epoch = int(epoch) if epoch is not None else 1
        if current_epoch != token_epoch:
            raise HTTPException(401, detail={"reason": "epoch_mismatch"})

        # Mark jti used to prevent replay
        sess.add(UsedToken(jti=jti))
        await sess.commit()

    # Issue a normal session cookie + redirect to /
    session_token = issue_admin_token(
        owner_id=owner_id, auth_epoch=current_epoch, settings=settings
    )
    redirect = RedirectResponse(url="/", status_code=302)
    set_session_cookie(redirect, session_token)
    logger.info("magic-link redeemed; owner_id=%s jti=%s", owner_id, jti)
    return redirect


__all__ = ["router"]
