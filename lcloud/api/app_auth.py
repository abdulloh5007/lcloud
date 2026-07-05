"""Firebase-style end-user auth for publishable LCloud DB projects."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt as pyjwt
import sqlalchemy as sa
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from lcloud.auth import api_keys as owner_keys
from lcloud.auth.jwt_utils import ensure_jwt_secret
from lcloud.auth.v2_deps import _user_from_api_key, _user_from_cookie
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import AppRefreshSession, AppUser, JsonDbPublicKey, User
from lcloud.utils.rate_limit import RateLimiter

router = APIRouter(prefix="/api/v1/public/auth/key", tags=["app_auth"])

ACCESS_TOKEN_TTL_SECONDS = 15 * 60
REFRESH_TOKEN_TTL_DAYS = 365
AUTH_RATE_LIMIT = 30
AUTH_RATE_WINDOW_SECONDS = 60
_auth_rate = RateLimiter(capacity=AUTH_RATE_LIMIT, refill_seconds=AUTH_RATE_WINDOW_SECONDS)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


@dataclass(frozen=True)
class AppIdentity:
    id: int
    uid: str
    project_owner_user_id: int


@dataclass(frozen=True)
class PublicPrincipal:
    owner_user: User | None = None
    app_user: AppIdentity | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_refresh_token() -> str:
    return f"lcrt_{secrets.token_urlsafe(48)}"


def _issue_access_token(user: AppUser) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user.uid,
        "kind": "app_access",
        "app_user_id": user.id,
        "project_owner_user_id": user.project_owner_user_id,
        "provider": user.provider,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(payload, ensure_jwt_secret(), algorithm="HS256")


def _decode_access_token(token: str) -> AppIdentity:
    payload = pyjwt.decode(token, ensure_jwt_secret(), algorithms=["HS256"])
    if payload.get("kind") != "app_access":
        raise pyjwt.InvalidTokenError("not an app access token")
    return AppIdentity(
        id=int(payload["app_user_id"]),
        uid=str(payload["sub"]),
        project_owner_user_id=int(payload["project_owner_user_id"]),
    )


def _auth_response(user: AppUser, refresh_token: str) -> dict[str, Any]:
    return {
        "access_token": _issue_access_token(user),
        "refresh_token": refresh_token,
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "token_type": "Bearer",
        "user": {
            "uid": user.uid,
            "provider": user.provider,
            "is_anonymous": user.provider == "anonymous",
        },
    }


async def _project_for_key(key: str) -> JsonDbPublicKey:
    sm = get_sessionmaker()
    async with sm() as sess:
        row = (
            await sess.execute(
                sa.select(JsonDbPublicKey).where(
                    JsonDbPublicKey.key == key,
                    JsonDbPublicKey.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail={"reason": "public_key_not_found"})
        return row


def _enforce_rate(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    if not _auth_rate.try_acquire(ip):
        raise HTTPException(
            429,
            detail={
                "reason": "rate_limited",
                "scope": "app_auth",
                "limit": AUTH_RATE_LIMIT,
                "window_seconds": AUTH_RATE_WINDOW_SECONDS,
            },
        )


async def get_public_principal(
    lc_user_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> PublicPrincipal:
    owner_user: User | None = None
    app_user: AppIdentity | None = None

    if lc_user_session:
        owner_user = await _user_from_cookie(lc_user_session)

    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, detail={"reason": "invalid_credentials"})
        if owner_keys.looks_like_api_key(token):
            owner_user = await _user_from_api_key(token)
            if owner_user is None:
                raise HTTPException(401, detail={"reason": "invalid_credentials"})
        else:
            try:
                app_user = _decode_access_token(token)
            except pyjwt.ExpiredSignatureError:
                raise HTTPException(401, detail={"reason": "app_token_expired"}) from None
            except (pyjwt.PyJWTError, KeyError, TypeError, ValueError):
                raise HTTPException(401, detail={"reason": "invalid_app_token"}) from None

    return PublicPrincipal(owner_user=owner_user, app_user=app_user)


OptionalPublicPrincipal = Annotated[PublicPrincipal, Depends(get_public_principal)]


@router.post("/{publishable_key}/anonymous", status_code=201)
async def sign_in_anonymously(
    publishable_key: str,
    request: Request,
) -> dict[str, Any]:
    _enforce_rate(request)
    project = await _project_for_key(publishable_key)
    now = _now()
    refresh_token = _new_refresh_token()
    sm = get_sessionmaker()
    async with sm() as sess:
        user = AppUser(
            project_owner_user_id=project.owner_user_id,
            uid=f"anon_{secrets.token_urlsafe(18)}",
            provider="anonymous",
            last_seen_at=now,
        )
        sess.add(user)
        await sess.flush()
        sess.add(
            AppRefreshSession(
                app_user_id=user.id,
                token_hash=_token_hash(refresh_token),
                last_used_at=now,
                expires_at=now + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
            )
        )
        await sess.commit()
        await sess.refresh(user)
    return _auth_response(user, refresh_token)


@router.post("/{publishable_key}/refresh")
async def refresh_session(
    publishable_key: str,
    body: RefreshIn,
    request: Request,
) -> dict[str, Any]:
    _enforce_rate(request)
    project = await _project_for_key(publishable_key)
    now = _now()
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(AppRefreshSession, AppUser)
            .join(AppUser, AppUser.id == AppRefreshSession.app_user_id)
            .where(AppRefreshSession.token_hash == _token_hash(body.refresh_token))
        )
        pair = result.one_or_none()
        if pair is None:
            raise HTTPException(401, detail={"reason": "invalid_refresh_token"})
        session, user = pair
        if (
            session.revoked_at is not None
            or _as_utc(session.expires_at) <= now
            or user.disabled_at is not None
            or user.project_owner_user_id != project.owner_user_id
        ):
            raise HTTPException(401, detail={"reason": "invalid_refresh_token"})

        session.last_used_at = now
        session.expires_at = now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)
        user.last_seen_at = now
        await sess.commit()
        await sess.refresh(user)
    return _auth_response(user, body.refresh_token)


@router.post(
    "/{publishable_key}/sign-out",
    status_code=204,
    response_class=Response,
)
async def sign_out(
    publishable_key: str,
    body: RefreshIn,
    request: Request,
) -> Response:
    _enforce_rate(request)
    project = await _project_for_key(publishable_key)
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            sa.select(AppRefreshSession, AppUser)
            .join(AppUser, AppUser.id == AppRefreshSession.app_user_id)
            .where(AppRefreshSession.token_hash == _token_hash(body.refresh_token))
        )
        pair = result.one_or_none()
        if pair is not None:
            session, user = pair
            if user.project_owner_user_id == project.owner_user_id:
                session.revoked_at = _now()
                await sess.commit()
    return Response(status_code=204)


@router.get("/{publishable_key}/me")
async def app_auth_me(
    publishable_key: str,
    principal: OptionalPublicPrincipal,
) -> dict[str, Any]:
    project = await _project_for_key(publishable_key)
    user = principal.app_user
    if user is None or user.project_owner_user_id != project.owner_user_id:
        raise HTTPException(401, detail={"reason": "app_auth_required"})
    return {"uid": user.uid, "provider": "anonymous", "is_anonymous": True}


def reset_app_auth_rate_limit() -> None:
    _auth_rate.reset()
