"""V2 auth: BIP39 / Ed25519 challenge-response login.

Flow:
    POST /auth/v2/challenge {pubkey_hex}
        ↓ server returns a server-signed JWT containing a fresh nonce + exp
    [client signs the raw `nonce` (hex bytes) with their Ed25519 privkey]
    POST /auth/v2/verify {challenge_jwt, signature_hex}
        ↓ server verifies sig over nonce against pubkey claimed in jwt;
          checks user exists (or registers if first time);
          issues lc_user_session cookie

The server never sees the seed phrase or private key — only the public key
and the detached signature.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import jwt as pyjwt
import sqlalchemy as sa
from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from lcloud.auth.jwt_utils import ensure_jwt_secret
from lcloud.auth.seed import verify_signature
from lcloud.cache import cache, k_user_me
from lcloud.config import Settings, get_settings
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import AuthChallenge, User
from lcloud.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/v2", tags=["auth_v2"])

# IP-keyed rate limit: 10 attempts / 5 min total across challenge+verify
_v2_rate = RateLimiter(capacity=10, refill_seconds=300.0)

USER_COOKIE_NAME = "lc_user_session"
CHALLENGE_TTL_SECONDS = 60
SESSION_TTL_SECONDS = 7 * 24 * 3600


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_rate(request: Request) -> None:
    if not _v2_rate.try_acquire(_client_ip(request)):
        raise HTTPException(429, detail={"reason": "rate_limited"})


# ------------------------------------------------------------------ schemas


class ChallengeIn(BaseModel):
    pubkey: str = Field(min_length=64, max_length=64, description="32-byte Ed25519 pubkey, hex")

    @field_validator("pubkey")
    @classmethod
    def _hex_ok(cls, v: str) -> str:
        try:
            bytes.fromhex(v)
        except ValueError as exc:
            raise ValueError("pubkey must be 64 lowercase hex chars (32 bytes)") from exc
        return v.lower()


class VerifyIn(BaseModel):
    challenge_jwt: str = Field(min_length=20, max_length=4096)
    signature: str = Field(
        min_length=128, max_length=128, description="64-byte Ed25519 signature, hex"
    )

    @field_validator("signature")
    @classmethod
    def _sig_ok(cls, v: str) -> str:
        try:
            bytes.fromhex(v)
        except ValueError as exc:
            raise ValueError("signature must be 128 lowercase hex chars (64 bytes)") from exc
        return v.lower()


# ------------------------------------------------------------------ helpers


def _user_session_cookie(
    *, user_id: int, role: str, settings: Settings, now: int | None = None
) -> str:
    """HS256 JWT used as the V2 session cookie."""
    secret = ensure_jwt_secret(settings)
    iat = now if now is not None else int(time.time())
    payload: dict[str, Any] = {
        "sub": "user",
        "kind": "user_session",
        "user_id": user_id,
        "role": role,
        "iat": iat,
        "exp": iat + SESSION_TTL_SECONDS,
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


def decode_user_session(
    token: str, settings: Settings | None = None
) -> dict[str, Any]:
    s = settings or get_settings()
    secret = ensure_jwt_secret(s)
    payload: dict[str, Any] = pyjwt.decode(token, secret, algorithms=["HS256"])
    if payload.get("sub") != "user" or payload.get("kind") != "user_session":
        raise pyjwt.InvalidTokenError("not a user_session token")
    return payload


def _challenge_jwt(
    *, pubkey_hex: str, nonce: str, settings: Settings
) -> str:
    """Server-signed wrapper around the nonce so /verify is stateless-ish."""
    secret = ensure_jwt_secret(settings)
    iat = int(time.time())
    payload: dict[str, Any] = {
        "kind": "auth_challenge",
        "pubkey": pubkey_hex,
        "nonce": nonce,
        "iat": iat,
        "exp": iat + CHALLENGE_TTL_SECONDS,
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _decode_challenge_jwt(
    token: str, settings: Settings
) -> dict[str, Any]:
    secret = ensure_jwt_secret(settings)
    payload: dict[str, Any] = pyjwt.decode(token, secret, algorithms=["HS256"])
    if payload.get("kind") != "auth_challenge":
        raise pyjwt.InvalidTokenError("wrong kind")
    return payload


# ------------------------------------------------------------------ endpoints


@router.post(
    "/challenge",
    summary="Запросить challenge для входа",
    description=(
        "**Шаг 1 из 2** при входе через seed-phrase.\n\n"
        "Клиент посылает свой публичный ключ (32 байта Ed25519, hex 64 chars). "
        "Сервер отвечает случайным nonce + signed JWT с этим nonce. "
        "Клиент должен подписать сырой `nonce` своим приватным ключом и "
        "отправить `signature` + `challenge_jwt` в `/auth/v2/verify`.\n\n"
        "**Rate limit**: 10 запросов / 5 минут / IP."
    ),
    responses={
        200: {
            "description": "Challenge выпущен",
            "content": {
                "application/json": {
                    "example": {
                        "challenge_jwt": "eyJhbGc...",
                        "nonce": "1f5a8c...64hex",
                        "expires_in": 60,
                    }
                }
            },
        },
        429: {"description": "Rate limit превышен"},
    },
)
async def post_challenge(
    body: ChallengeIn, request: Request
) -> dict[str, Any]:
    """Issue a fresh nonce. The client signs `nonce_bytes` (hex-decoded) with
    their Ed25519 privkey and posts the result to /verify."""
    _enforce_rate(request)
    pubkey_hex = body.pubkey
    nonce = secrets.token_hex(32)
    settings = get_settings()

    sm = get_sessionmaker()
    async with sm() as sess:
        sess.add(
            AuthChallenge(
                nonce=nonce,
                pubkey=bytes.fromhex(pubkey_hex),
                expires_at=datetime.now(UTC).fromtimestamp(
                    time.time() + CHALLENGE_TTL_SECONDS, tz=UTC
                ),
            )
        )
        await sess.commit()

    challenge_jwt = _challenge_jwt(
        pubkey_hex=pubkey_hex, nonce=nonce, settings=settings
    )
    return {
        "challenge_jwt": challenge_jwt,
        "nonce": nonce,
        "expires_in": CHALLENGE_TTL_SECONDS,
    }


@router.post(
    "/verify",
    summary="Проверить подпись и войти",
    description=(
        "**Шаг 2 из 2** при входе через seed-phrase.\n\n"
        "Клиент: 1) декодирует `nonce` из `/challenge`, 2) подписывает его "
        "Ed25519 приватным ключом, 3) шлёт challenge_jwt + signature сюда. "
        "Сервер проверяет:\n\n"
        "- `challenge_jwt` валиден и не истёк (60 сек TTL)\n"
        "- nonce ещё не использовался (replay protection)\n"
        "- Ed25519 signature валиден для pubkey из jwt\n\n"
        "Если pubkey ещё не зарегистрирован — создаётся новая запись users "
        "автоматически (`registered: true`).\n\n"
        "Устанавливает cookie `lc_user_session` (HS256 JWT, 7 дней)."
    ),
    responses={
        200: {
            "description": "Авторизован — cookie установлен",
            "content": {
                "application/json": {
                    "example": {
                        "user_id": 42,
                        "role": "user",
                        "registered": False,
                    }
                }
            },
        },
        401: {"description": "Подпись невалидна / challenge истёк / replay"},
        403: {"description": "Аккаунт заблокирован"},
    },
)
async def post_verify(
    body: VerifyIn, request: Request, response: Response
) -> dict[str, Any]:
    """Verify the client's signature; auto-register if user not yet seen.

    Returns ``{user_id, role, registered: bool}`` and sets the
    `lc_user_session` cookie.
    """
    _enforce_rate(request)
    settings = get_settings()
    try:
        claims = _decode_challenge_jwt(body.challenge_jwt, settings)
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(401, detail={"reason": "challenge_expired"}) from exc
    except pyjwt.PyJWTError as exc:
        raise HTTPException(
            401, detail={"reason": "invalid_challenge"}
        ) from exc

    pubkey_hex = str(claims["pubkey"])
    nonce_hex = str(claims["nonce"])
    pubkey = bytes.fromhex(pubkey_hex)
    nonce_bytes = bytes.fromhex(nonce_hex)
    sig_bytes = bytes.fromhex(body.signature)

    if not verify_signature(pubkey, nonce_bytes, sig_bytes):
        raise HTTPException(401, detail={"reason": "bad_signature"})

    sm = get_sessionmaker()
    async with sm() as sess:
        # Single-use enforcement: mark nonce consumed, refuse if already
        ch = (
            await sess.execute(
                sa.select(AuthChallenge).where(AuthChallenge.nonce == nonce_hex)
            )
        ).scalar_one_or_none()
        if ch is None:
            raise HTTPException(401, detail={"reason": "challenge_unknown"})
        if ch.consumed_at is not None:
            raise HTTPException(401, detail={"reason": "challenge_replay"})
        ch.consumed_at = datetime.now(UTC)

        # Find or create the user
        user = (
            await sess.execute(
                sa.select(User).where(User.pubkey == pubkey)
            )
        ).scalar_one_or_none()
        registered = False
        if user is None:
            user = User(pubkey=pubkey, role="user")
            sess.add(user)
            registered = True
            logger.info(
                "registered new user pubkey=%s...", pubkey.hex()[:16]
            )

        if user.suspended_at is not None:
            await sess.commit()
            raise HTTPException(403, detail={"reason": "suspended"})

        await sess.commit()
        await sess.refresh(user)
        user_id = user.id
        role = user.role

    # Issue session cookie
    cookie_value = _user_session_cookie(
        user_id=user_id, role=role, settings=settings
    )
    response.set_cookie(
        key=USER_COOKIE_NAME,
        value=cookie_value,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=settings.lc_cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"user_id": user_id, "role": role, "registered": registered}


@router.post(
    "/logout",
    summary="Выйти из сессии",
    description="Удаляет cookie `lc_user_session`. Браузер становится анонимным.",
)
async def post_logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(key=USER_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get(
    "/me",
    summary="Текущий пользователь",
    description=(
        "Возвращает идентичность залогиненного пользователя: pubkey, role, "
        "quota usage, дата создания. Принимает cookie `lc_user_session` или "
        "`Authorization: Bearer lc-XXX...`.\n\n"
        "Используйте этот эндпоинт чтобы понять, валидна ли ваша сессия.\n\n"
        "_Кешируется на 60 сек по user_id._"
    ),
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "user_id": 42,
                        "role": "user",
                        "pubkey": "5eb36f5d...64hex",
                        "label": None,
                        "storage_used_bytes": 1234567,
                        "storage_quota_bytes": 5368709120,
                        "created_at": "2026-05-30T08:00:00+00:00",
                    }
                }
            }
        },
        401: {"description": "Нет валидного токена"},
    },
)
async def get_me(
    lc_user_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    if not lc_user_session:
        raise HTTPException(401, detail={"reason": "no_session"})
    try:
        payload = decode_user_session(lc_user_session)
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(401, detail={"reason": "expired"}) from exc
    except pyjwt.PyJWTError as exc:
        raise HTTPException(401, detail={"reason": "invalid_session"}) from exc

    user_id = int(payload["user_id"])

    # Cache hit?
    cached = await cache.get(k_user_me(user_id))
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    sm = get_sessionmaker()
    async with sm() as sess:
        user = (
            await sess.execute(
                sa.select(User).where(User.id == user_id)
            )
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(401, detail={"reason": "user_missing"})
        if user.suspended_at is not None:
            raise HTTPException(403, detail={"reason": "suspended"})

    body = {
        "user_id": user.id,
        "role": user.role,
        "pubkey": user.pubkey.hex(),
        "label": user.label,
        "storage_used_bytes": user.storage_used_bytes,
        "storage_quota_bytes": user.storage_quota_bytes,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
    await cache.set(k_user_me(user_id), body, ttl=60.0)
    return body


__all__ = [
    "USER_COOKIE_NAME",
    "_user_session_cookie",
    "decode_user_session",
    "router",
]
