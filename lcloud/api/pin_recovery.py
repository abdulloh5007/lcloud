"""V2 PIN-recovery endpoints.

Two operations:

POST /auth/v2/pin/setup     (authenticated user)
    Body: { pin: "1234", mnemonic: "abandon ability ... (12 or 24 words)" }
    Server:
      - Validates PIN format and seed phrase
      - Derives Ed25519 from the seed and confirms it matches the
        logged-in user's pubkey (sanity check — they really are the
        owner)
      - argon2.hash(pin) → users.pin_hash
      - random 16B salt → users.seed_salt
      - SecretBox(kdf(pin, salt)).encrypt(mnemonic) → users.encrypted_seed
      - Resets pin_failed_attempts/locked_until
    Returns 204.

POST /auth/v2/pin/recover    (anonymous, rate-limited)
    Body: { contact_handle: "@user", pin: "1234" }
    Server:
      - Rate-limit by IP: 10/h
      - Find user by contact_handle (set during admin approval)
      - Check pin_locked_until — refuse if still locked
      - argon2.verify(pin) — on miss, increment pin_failed_attempts;
        if ≥5, set pin_locked_until = +1h and refuse
      - On hit: reset attempts; decrypt seed; return mnemonic
    Returns 200 with the seed phrase (browser shows it once).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Cookie, HTTPException, Request
from pydantic import BaseModel, Field

from lcloud.auth import pin_recovery as pin
from lcloud.auth.seed import derive_keypair, is_valid_mnemonic
from lcloud.cache import cache, k_user_me
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import User
from lcloud.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/v2/pin", tags=["auth_v2"])

# Anonymous IP rate limit on /recover — 10 attempts / hour / IP.
_recover_rate = RateLimiter(capacity=10, refill_seconds=3600.0)


class SetupIn(BaseModel):
    pin: str = Field(min_length=4, max_length=4, description="Exactly 4 digits.")
    mnemonic: str = Field(min_length=20, max_length=400)


class RecoverIn(BaseModel):
    contact_handle: str = Field(min_length=2, max_length=128)
    pin: str = Field(min_length=4, max_length=4)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ----------------------------------------------------------- setup


@router.post(
    "/setup",
    summary="Поставить PIN на восстановление сид-фразы",
    description=(
        "Сохраняет сид-фразу зашифрованной с помощью 4-значного PIN. "
        "Если пользователь забудет/потеряет слова — он может вернуть их "
        "через `/auth/v2/pin/recover`, зная свой PIN.\n\n"
        "Слова и PIN живут в браузере только во время этого запроса; "
        "сервер хранит шифр + соль + хеш PIN.\n\n"
        "Требует валидной сессии. Сид-фраза должна реально принадлежать "
        "залогиненному пользователю (проверяется через сравнение pubkey)."
    ),
)
async def setup_pin(
    body: SetupIn,
    lc_user_session: Annotated[str | None, Cookie()] = None,
) -> dict[str, bool]:
    if not lc_user_session:
        raise HTTPException(401, detail={"reason": "no_session"})

    # Local import to avoid the circular through lcloud.api.auth_v2 → lcloud.auth.deps
    from lcloud.api.auth_v2 import decode_user_session

    try:
        payload = decode_user_session(lc_user_session)
    except Exception as exc:  # ExpiredSignature, InvalidToken, etc.
        raise HTTPException(401, detail={"reason": "invalid_session"}) from exc

    user_id = int(payload["user_id"])

    # Validate PIN strict
    if not pin.is_valid_pin(body.pin):
        raise HTTPException(400, detail={"reason": "bad_pin_format"})

    # Validate mnemonic (BIP39 checksum)
    mnemonic_clean = body.mnemonic.strip()
    if not is_valid_mnemonic(mnemonic_clean):
        raise HTTPException(400, detail={"reason": "bad_mnemonic"})

    # Sanity: derived pubkey must match the logged-in user — otherwise
    # the user is trying to back up someone else's seed.
    derived = derive_keypair(mnemonic_clean)

    sm = get_sessionmaker()
    async with sm() as sess:
        user = (
            await sess.execute(sa.select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(401, detail={"reason": "user_missing"})
        if user.pubkey != derived.pubkey:
            raise HTTPException(
                400, detail={"reason": "mnemonic_does_not_match_pubkey"}
            )

        salt, ciphertext = pin.encrypt_seed(body.pin, mnemonic_clean)
        user.pin_hash = pin.hash_pin(body.pin)
        user.seed_salt = salt
        user.encrypted_seed = ciphertext
        user.pin_failed_attempts = 0
        user.pin_locked_until = None
        await sess.commit()

    await cache.delete(k_user_me(user_id))
    logger.info("user_id=%d set up PIN-protected seed recovery", user_id)
    return {"ok": True}


# ----------------------------------------------------------- recover


@router.post(
    "/recover",
    summary="Восстановить сид-фразу по PIN",
    description=(
        "Возвращает сид-фразу пользователя если он знает свой 4-значный PIN. "
        "Требует, чтобы пользователь предварительно настроил PIN через `/setup`. "
        "Анонимный, идентифицирует пользователя по `contact_handle` (тот же, "
        "что был указан при покупке аккаунта).\n\n"
        "**Защита:**\n"
        "- IP rate limit: 10 попыток / час / IP\n"
        "- 5 неверных PIN → блокировка пользователя на 1 час\n"
        "- Сид показывается ОДИН раз — сохраните сразу.\n\n"
        "Если PIN потерян — обратиться к админу за полным сбросом аккаунта."
    ),
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "mnemonic": "abandon ability able about ... art",
                        "warning": "Save these words now — sent only this once.",
                    }
                }
            }
        },
        401: {"description": "Неверный PIN"},
        403: {"description": "Аккаунт заблокирован после 5 неверных попыток"},
        404: {"description": "Пользователь не найден или PIN не настроен"},
        429: {"description": "Rate limit (IP) превышен"},
    },
)
async def recover_seed(
    body: RecoverIn, request: Request
) -> dict[str, str]:
    if not _recover_rate.try_acquire(_client_ip(request)):
        raise HTTPException(429, detail={"reason": "rate_limited"})

    if not pin.is_valid_pin(body.pin):
        raise HTTPException(400, detail={"reason": "bad_pin_format"})

    sm = get_sessionmaker()
    async with sm() as sess:
        user = (
            await sess.execute(
                sa.select(User).where(User.contact_handle == body.contact_handle)
            )
        ).scalar_one_or_none()
        # We don't reveal whether the contact exists — generic 'not_found'
        if user is None or user.pin_hash is None or user.encrypted_seed is None:
            raise HTTPException(404, detail={"reason": "not_found"})

        # Lockout check
        now = datetime.now(UTC)
        locked_until = user.pin_locked_until
        # SQLite returns naive datetimes even with timezone=True; assume UTC
        if locked_until is not None and locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=UTC)
        if locked_until is not None and locked_until > now:
            secs_left = int((locked_until - now).total_seconds())
            raise HTTPException(
                403,
                detail={"reason": "locked", "retry_after_seconds": secs_left},
            )

        # PIN verify (slow, constant-time)
        if not pin.verify_pin(body.pin, user.pin_hash):
            user.pin_failed_attempts = (user.pin_failed_attempts or 0) + 1
            if user.pin_failed_attempts >= pin.MAX_FAILED_ATTEMPTS:
                user.pin_locked_until = now + timedelta(seconds=pin.LOCKOUT_SECONDS)
                await sess.commit()
                raise HTTPException(
                    403,
                    detail={
                        "reason": "locked",
                        "retry_after_seconds": pin.LOCKOUT_SECONDS,
                    },
                )
            await sess.commit()
            attempts_left = pin.MAX_FAILED_ATTEMPTS - user.pin_failed_attempts
            raise HTTPException(
                401,
                detail={"reason": "wrong_pin", "attempts_left": attempts_left},
            )

        # On success: reset counters and decrypt
        if user.seed_salt is None:
            raise HTTPException(404, detail={"reason": "not_found"})

        seed = pin.decrypt_seed(body.pin, user.seed_salt, user.encrypted_seed)
        if seed is None:
            # Should never happen if pin_hash matched — but defensively
            raise HTTPException(500, detail={"reason": "decrypt_failed"})

        user.pin_failed_attempts = 0
        user.pin_locked_until = None
        await sess.commit()
        logger.info(
            "user_id=%d successfully recovered seed phrase via PIN",
            user.id,
        )

    return {
        "mnemonic": seed,
        "warning": (
            "Сохрани эти слова сейчас — другой раз через PIN их можно "
            "будет получить, но безопаснее иметь оффлайн копию."
        ),
    }


__all__ = ["router"]
