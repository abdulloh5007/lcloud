"""Payment + admin-approval flow.

Endpoints (public, no auth):
  POST /api/v1/payments/request
       — submit a "I paid, please give me an account" request.
       Body: {contact_handle, note?}
       Server queues `payment_requests` row (status=pending).

Endpoints (admin only — V2 cookie/Bearer with role='admin'):
  GET  /api/v1/admin/payments
       — list pending+approved+rejected requests (filterable).
  POST /api/v1/admin/payments/{id}/approve
       — generate a fresh BIP39 seed phrase + Ed25519 keypair, create
       the User row, mark request approved, return seed_phrase to the
       admin **once**. Admin must deliver to the user (e.g. via Telegram).
  POST /api/v1/admin/payments/{id}/reject
       — mark request rejected with optional reason.

Why this flow:
  - LCloud is now paid; we don't auto-create accounts. The admin
    manually verifies bank-transfer payment (card 4413... display in UI)
    and approves.
  - Server briefly knows the seed phrase between approval and admin
    delivering it. Once delivered, the user can rotate it via
    seed-phrase recovery (PIN flow, separate file).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from lcloud.auth.seed import derive_keypair, generate_mnemonic
from lcloud.auth.v2_deps import CurrentUserAdmin
from lcloud.db.base import get_sessionmaker
from lcloud.db.models import PaymentRequest, User
from lcloud.metrics import payment_decisions_counter, payment_requests_counter
from lcloud.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

# Rate-limit anonymous payment requests to deter abuse.
# 5 requests per IP per hour.
_pay_rate = RateLimiter(capacity=5, refill_seconds=3600.0)

# Public router — no auth (anyone can submit a request).
public_router = APIRouter(prefix="/api/v1/payments", tags=["payments"])
# Admin router — gated by V2 admin role.
admin_router = APIRouter(prefix="/api/v1/admin/payments", tags=["payments_admin"])


# ----------------------------------------------------------- payment metadata


# Static payment instructions exposed to the buyer's UI. Kept here (not in
# .env) because they're public anyway.
PAYMENT_CARD = {
    "card_number": "4413597603239011",
    "card_holder": "Abdulloh Ergashev",
    "scheme": "Visa",
    "amount_cents": 700,
    "currency": "USD",
    "tier_label": "5 GB lifetime",
}


# ----------------------------------------------------------- schemas


class PaymentRequestIn(BaseModel):
    contact_handle: str = Field(min_length=2, max_length=128)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("contact_handle")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("contact_handle must not be empty")
        return v


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


# ----------------------------------------------------------- helpers


def _serialize_request(req: PaymentRequest, *, include_user: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": req.id,
        "contact_handle": req.contact_handle,
        "amount_cents": req.amount_cents,
        "currency": req.currency,
        "note": req.note,
        "status": req.status,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "approved_at": req.approved_at.isoformat() if req.approved_at else None,
        "rejected_at": req.rejected_at.isoformat() if req.rejected_at else None,
        "generated_user_id": req.generated_user_id,
        "ip_addr": req.ip_addr,
    }
    return out


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ----------------------------------------------------------- public endpoints


@public_router.get(
    "/info",
    summary="Платежная информация",
    description=(
        "Возвращает данные карты для оплаты и текущий тариф. "
        "Не требует авторизации."
    ),
)
async def payment_info() -> dict[str, Any]:
    return PAYMENT_CARD


@public_router.post(
    "/request",
    status_code=201,
    summary="Подать заявку на аккаунт",
    description=(
        "После оплаты по карте через банк/перевод — отправьте сюда свой "
        "контакт (telegram username, email или phone), чтобы админ "
        "выдал аккаунт. Заявка попадает в админскую очередь.\n\n"
        "Rate limit: 5 заявок / час / IP."
    ),
)
async def submit_request(
    body: PaymentRequestIn, request: Request
) -> dict[str, Any]:
    if not _pay_rate.try_acquire(_client_ip(request)):
        payment_requests_counter.labels(outcome="rate_limited").inc()
        raise HTTPException(429, detail={"reason": "rate_limited"})

    sm = get_sessionmaker()
    async with sm() as sess:
        existing = (
            await sess.execute(
                sa.select(PaymentRequest).where(
                    PaymentRequest.contact_handle == body.contact_handle,
                    PaymentRequest.status == "pending",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            payment_requests_counter.labels(outcome="duplicate").inc()
            logger.info(
                "duplicate pending payment request from %s (id=%d)",
                body.contact_handle,
                existing.id,
            )
            return {
                "id": existing.id,
                "status": existing.status,
                "duplicate": True,
            }

        req = PaymentRequest(
            contact_handle=body.contact_handle,
            note=body.note,
            amount_cents=PAYMENT_CARD["amount_cents"],
            currency=PAYMENT_CARD["currency"],
            ip_addr=_client_ip(request),
        )
        sess.add(req)
        await sess.commit()
        await sess.refresh(req)
        payment_requests_counter.labels(outcome="submitted").inc()
        logger.info(
            "new payment request id=%d contact=%s ip=%s",
            req.id,
            body.contact_handle,
            req.ip_addr,
        )
        return {
            "id": req.id,
            "status": req.status,
            "duplicate": False,
        }


# ----------------------------------------------------------- admin endpoints


@admin_router.get(
    "",
    summary="Список заявок",
    description=(
        "Все заявки на покупку аккаунта. Параметр `status` фильтрует: "
        "`pending`, `approved`, `rejected`. По умолчанию — все."
    ),
)
async def list_requests(
    admin: CurrentUserAdmin,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if status not in (None, "pending", "approved", "rejected"):
        raise HTTPException(400, detail={"reason": "bad_status"})

    sm = get_sessionmaker()
    async with sm() as sess:
        stmt = sa.select(PaymentRequest).order_by(
            PaymentRequest.created_at.desc()
        )
        if status:
            stmt = stmt.where(PaymentRequest.status == status)
        rows = (await sess.execute(stmt)).scalars().all()
    return [_serialize_request(r) for r in rows]


@admin_router.post(
    "/{request_id}/approve",
    summary="Одобрить заявку и выдать сид-фразу",
    description=(
        "Генерирует свежую 24-словную BIP39 сид-фразу + Ed25519 keypair, "
        "создаёт User-запись, помечает заявку approved, **возвращает сид "
        "админу один раз**. Админ должен передать слова покупателю "
        "(например, через Telegram DM).\n\n"
        "После того как пользователь войдёт первый раз, ему стоит "
        "включить PIN-восстановление (см. `/auth/v2/pin/setup`)."
    ),
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "request_id": 5,
                        "user_id": 42,
                        "contact_handle": "@buyer",
                        "seed_phrase": "abandon ability ... art (24 words)",
                        "warning": "Save & deliver — shown once.",
                    }
                }
            }
        },
        404: {"description": "Заявка не найдена"},
        409: {"description": "Заявка уже обработана"},
    },
)
async def approve_request(
    request_id: int, admin: CurrentUserAdmin
) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        req = (
            await sess.execute(
                sa.select(PaymentRequest).where(PaymentRequest.id == request_id)
            )
        ).scalar_one_or_none()
        if req is None:
            raise HTTPException(404, detail={"reason": "not_found"})
        if req.status != "pending":
            raise HTTPException(
                409, detail={"reason": "already_processed", "status": req.status}
            )

        # Generate the keypair NOW (server knows seed during this brief moment)
        mnemonic = generate_mnemonic(words=24)
        ident = derive_keypair(mnemonic)

        # Make sure pubkey isn't already registered (cosmically unlikely but…)
        clash = (
            await sess.execute(
                sa.select(User).where(User.pubkey == ident.pubkey)
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                500, detail={"reason": "pubkey_clash", "retry_recommended": True}
            )

        user = User(
            pubkey=ident.pubkey,
            role="user",
            label=req.contact_handle[:64],
            contact_handle=req.contact_handle,
            paid_until=None,  # NULL = lifetime
            storage_quota_bytes=5 * 1024 * 1024 * 1024,  # 5 GB
        )
        sess.add(user)
        await sess.flush()

        req.status = "approved"
        req.approved_at = datetime.now(UTC)
        req.generated_user_id = user.id
        await sess.commit()
        payment_decisions_counter.labels(decision="approved").inc()
        logger.info(
            "approved payment request id=%d → user_id=%d contact=%s admin=%d",
            req.id,
            user.id,
            req.contact_handle,
            admin.id,
        )

    return {
        "request_id": request_id,
        "user_id": user.id,
        "contact_handle": req.contact_handle,
        "seed_phrase": mnemonic,
        "warning": (
            "Эта сид-фраза показывается ОДИН раз. "
            "Скопируй и отправь пользователю безопасно. "
            "Сервер её больше не сохраняет."
        ),
    }


@admin_router.post(
    "/{request_id}/reject",
    summary="Отклонить заявку",
)
async def reject_request(
    request_id: int, body: RejectIn, admin: CurrentUserAdmin
) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as sess:
        req = (
            await sess.execute(
                sa.select(PaymentRequest).where(PaymentRequest.id == request_id)
            )
        ).scalar_one_or_none()
        if req is None:
            raise HTTPException(404, detail={"reason": "not_found"})
        if req.status != "pending":
            raise HTTPException(
                409, detail={"reason": "already_processed", "status": req.status}
            )
        req.status = "rejected"
        req.rejected_at = datetime.now(UTC)
        if body.reason:
            req.note = (req.note or "") + f"\n[reject reason: {body.reason}]"
        await sess.commit()
        payment_decisions_counter.labels(decision="rejected").inc()
        logger.info(
            "rejected payment request id=%d admin=%d reason=%s",
            req.id,
            admin.id,
            body.reason or "(none)",
        )
    return {"request_id": request_id, "status": "rejected"}


__all__ = ["PAYMENT_CARD", "admin_router", "public_router"]
