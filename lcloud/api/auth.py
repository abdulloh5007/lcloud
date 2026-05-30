"""Auth router: web-based Telegram login flow + cookie issuance + state."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from lcloud.auth.cookies import clear_session_cookie, set_session_cookie
from lcloud.auth.jwt_utils import decode_admin_token, issue_admin_token
from lcloud.config import get_settings
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import AuthState, Owner
from lcloud.userbot.client import (
    AuthSnapshot,
    FlowAlreadyActiveError,
    LoginAlreadyAuthorizedError,
    LoginFlowState,
    NoActiveFlowError,
    UserbotManager,
    UserbotNotConfiguredError,
    WrongAccountError,
    get_userbot_manager,
)
from lcloud.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# 5 attempts per 5 minutes per IP across the whole login flow
_login_rl = RateLimiter(capacity=5, refill_seconds=300.0)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_rate(request: Request) -> None:
    ip = _client_ip(request)
    if not _login_rl.try_acquire(ip):
        raise HTTPException(429, detail={"reason": "rate_limited"})


def get_login_rate_limiter() -> RateLimiter:
    """Exposed for tests that want to reset the limiter between cases."""
    return _login_rl


# ------------------------------------------------------------------ schemas


class StartLoginIn(BaseModel):
    phone: str = Field(min_length=5, max_length=32)


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=512)


# ------------------------------------------------------------------ helpers


async def _resolve_admin_owner_id() -> tuple[int, int]:
    """Return (owner_id, current_auth_epoch) for the single admin owner."""
    sm = get_sessionmaker()
    async with sm() as sess:
        result = await sess.execute(
            select(Owner).where(Owner.role == "admin").limit(1)
        )
        owner = result.scalar_one_or_none()
        if owner is None:
            raise HTTPException(500, detail={"reason": "admin_owner_missing"})
        epoch_result = await sess.execute(
            select(AuthState.epoch).where(AuthState.owner_id == owner.id)
        )
        epoch = epoch_result.scalar_one_or_none() or 1
    return owner.id, epoch


async def _issue_session_cookie(
    response: Response, snap: AuthSnapshot
) -> dict[str, Any]:
    owner_id, epoch = await _resolve_admin_owner_id()
    token = issue_admin_token(owner_id=owner_id, auth_epoch=epoch)
    set_session_cookie(response, token)
    # Kick off a background dialog scan now that the userbot is admin-authorized.
    # Imported lazily to avoid a circular: main.py also imports from this module.
    from lcloud.main import _post_login_scan_if_authorized

    try:
        await _post_login_scan_if_authorized()
    except Exception:
        logger.exception("post-login scan trigger failed (non-fatal)")
    return {
        "authorized": True,
        "me": {
            "id": snap.me_id,
            "first_name": snap.me_first_name,
            "username": snap.me_username,
        },
    }


def _wrong_account_http(exc: WrongAccountError) -> HTTPException:
    return HTTPException(
        403,
        detail={"reason": "wrong_account", "got": exc.got, "expected": exc.expected},
    )


# ------------------------------------------------------------------ endpoints


@router.get("/state")
async def auth_state(
    manager: UserbotManager = Depends(get_userbot_manager),
    lc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Combined auth state for the SPA.

    Returns:
        authorized       — *real* auth = cookie present, valid, current
                           epoch, AND userbot is admin-authorized at TG.
                           This is what the UI keys off.
        userbot_authed   — telethon session itself is admin-authorized
                           (true even when the user has no cookie).
        bootstrap_mode   — no admin tg_id has been claimed yet; first
                           login wins, phone+code form should be shown.
        userbot_started  — telethon client is connected at all.
    """
    snap = await manager.snapshot()
    settings = get_settings()
    effective_admin = settings.effective_admin_tg_id()
    userbot_authed = (
        snap.authorized and snap.me_id is not None and snap.me_id == effective_admin
    )

    cookie_valid = False
    if lc_session and userbot_authed:
        try:
            payload = decode_admin_token(lc_session, settings=settings)
            if payload.get("sub") == "admin":
                # Verify epoch matches the current owner_id
                from sqlalchemy import select

                from lcloud.db.base import get_sessionmaker
                from lcloud.db.models import AuthState, Owner

                sm = get_sessionmaker()
                async with sm() as sess:
                    owner_row = (
                        await sess.execute(
                            select(Owner).where(Owner.role == "admin").limit(1)
                        )
                    ).scalar_one_or_none()
                    if owner_row is not None:
                        epoch = (
                            await sess.execute(
                                select(AuthState.epoch).where(
                                    AuthState.owner_id == owner_row.id
                                )
                            )
                        ).scalar_one_or_none()
                        if (
                            epoch is not None
                            and int(payload.get("ae", 0)) == int(epoch)
                            and int(payload.get("owner_id", 0)) == owner_row.id
                        ):
                            cookie_valid = True
        except Exception:
            cookie_valid = False

    return {
        "authorized": userbot_authed and cookie_valid,
        "userbot_authed": userbot_authed,
        "userbot_started": manager.is_started,
        "bootstrap_mode": effective_admin == 0,
        "state": snap.state.value,
        "me": (
            {
                "id": snap.me_id,
                "first_name": snap.me_first_name,
                "username": snap.me_username,
            }
            if userbot_authed and cookie_valid
            else None
        ),
    }


@router.post("/telegram/start")
async def telegram_start(
    body: StartLoginIn,
    request: Request,
    manager: UserbotManager = Depends(get_userbot_manager),
) -> dict[str, Any]:
    _enforce_rate(request)
    try:
        await manager.start_login(body.phone)
    except UserbotNotConfiguredError as exc:
        raise HTTPException(503, detail={"reason": "userbot_not_configured"}) from exc
    except LoginAlreadyAuthorizedError as exc:
        raise HTTPException(409, detail={"reason": "already_authorized"}) from exc
    except FlowAlreadyActiveError as exc:
        raise HTTPException(409, detail={"reason": "flow_already_active"}) from exc
    except Exception as exc:
        logger.exception("send_code_request failed")
        raise HTTPException(
            400, detail={"reason": "send_code_failed", "error": str(exc)}
        ) from exc
    return {"ok": True, "state": LoginFlowState.CODE_SENT.value}


@router.post("/telegram/code")
async def telegram_code(
    body: CodeIn,
    request: Request,
    response: Response,
    manager: UserbotManager = Depends(get_userbot_manager),
) -> dict[str, Any]:
    _enforce_rate(request)
    try:
        snap = await manager.submit_code(body.code)
    except NoActiveFlowError as exc:
        raise HTTPException(409, detail={"reason": "no_active_flow"}) from exc
    except WrongAccountError as exc:
        raise _wrong_account_http(exc) from exc
    except Exception as exc:
        logger.exception("sign_in (code) failed")
        raise HTTPException(
            400, detail={"reason": "sign_in_failed", "error": str(exc)}
        ) from exc
    if snap.state == LoginFlowState.PWD_NEEDED:
        return {"need_password": True, "state": snap.state.value}
    return await _issue_session_cookie(response, snap)


@router.post("/telegram/password")
async def telegram_password(
    body: PasswordIn,
    request: Request,
    response: Response,
    manager: UserbotManager = Depends(get_userbot_manager),
) -> dict[str, Any]:
    _enforce_rate(request)
    try:
        snap = await manager.submit_password(body.password)
    except NoActiveFlowError as exc:
        raise HTTPException(409, detail={"reason": "no_active_flow"}) from exc
    except WrongAccountError as exc:
        raise _wrong_account_http(exc) from exc
    except Exception as exc:
        logger.exception("sign_in (password) failed")
        raise HTTPException(
            400, detail={"reason": "password_failed", "error": str(exc)}
        ) from exc
    return await _issue_session_cookie(response, snap)


@router.post("/telegram/cancel")
async def telegram_cancel(
    manager: UserbotManager = Depends(get_userbot_manager),
) -> dict[str, Any]:
    await manager.cancel_flow()
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response) -> dict[str, Any]:
    clear_session_cookie(response)
    return {"ok": True}
