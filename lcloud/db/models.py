"""ORM models for LCloud (goal.md §10)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from lcloud.db.base import Base


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pubkey: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Cloud(Base):
    __tablename__ = "clouds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owners.id"), nullable=False, index=True
    )
    # V2 per-user owner; NULL = legacy admin (V1)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    about: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cloud_id: Mapped[int] = mapped_column(
        ForeignKey("clouds.id"), nullable=False, index=True
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owners.id"), nullable=False, index=True
    )
    # V2 per-user owner; NULL = legacy admin (V1)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(
        String(128), nullable=False, default="application/octet-stream"
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary(64), nullable=False)
    # V2: was the file re-encoded at upload time (lossy compression)?
    compressed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # V2: original (pre-compression) size in bytes; NULL = same as size_bytes
    original_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("cloud_id", "message_id", name="uq_files_cloud_msg"),
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str] = mapped_column(String(32), nullable=False)
    icon: Mapped[str] = mapped_column(String(64), nullable=False)
    bg_color: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_tags_owner_name"),
    )


class FileTag(Base):
    __tablename__ = "file_tags"

    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class UsedToken(Base):
    """Reserved for future magic-link revocation; not wired in V1 (web login replaces it)."""

    __tablename__ = "used_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthState(Base):
    """Per-owner monotonic epoch; bumped on /revoke to invalidate live cookies."""

    __tablename__ = "auth_state"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owners.id"), primary_key=True
    )
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class User(Base):
    """V2: registered identity, keyed by Ed25519 pubkey (BIP39-derived).

    No password — auth is challenge-response. `role='admin'` for the
    bootstrap user (whose pubkey is also stamped to data/keys/admin.pub
    and whose seed phrase was sent over Telegram on first run).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pubkey: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    storage_used_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    storage_quota_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=5 * 1024 * 1024 * 1024
    )
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Payments / recovery (added in 0004)
    contact_handle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    encrypted_seed: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    seed_salt: Mapped[bytes | None] = mapped_column(
        LargeBinary(16), nullable=True
    )
    pin_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pin_failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    pin_locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PaymentRequest(Base):
    """Queue of pending account-purchase requests awaiting admin review."""

    __tablename__ = "payment_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_handle: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=700)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    generated_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_payreq_user"), nullable=True
    )
    ip_addr: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ApiKey(Base):
    """Programmatic-access token. Hash-only (argon2 of raw key)."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hash: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthChallenge(Base):
    """Server-side login nonce, single-use, short-lived."""

    __tablename__ = "auth_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    pubkey: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
