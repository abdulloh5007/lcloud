"""Payment requests + PIN-protected seed recovery columns.

Revision ID: 0004_payments
Revises: 0003_compression
Create Date: 2026-05-30

Adds:

- `payment_requests` table — incoming requests from people who want
  an account. Admin reviews + approves; on approval, a User row is
  created and the generated seed phrase is shown to admin to deliver
  to the requester.
- `users.contact_handle` — telegram @username / email / phone the
  paying person provided. For delivery and recovery identification.
- `users.paid_until` — NULL means lifetime (current default).
- `users.encrypted_seed` + `users.seed_salt` + `users.pin_hash` +
  `users.pin_failed_attempts` + `users.pin_locked_until` — PIN-protected
  recovery storage. PIN is 4 digits; brute force resistance via argon2
  + per-user lockout after 5 wrong attempts.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_payments"
down_revision: str | None = "0003_compression"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- payment_requests table
    op.create_table(
        "payment_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("contact_handle", sa.String(128), nullable=False),
        sa.Column("amount_cents", sa.Integer, nullable=False, server_default="700"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "generated_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", name="fk_payreq_user"),
            nullable=True,
        ),
        sa.Column("ip_addr", sa.String(64), nullable=True),
    )
    op.create_index("ix_payment_requests_status", "payment_requests", ["status"])
    op.create_index(
        "ix_payment_requests_contact_handle",
        "payment_requests",
        ["contact_handle"],
    )

    # ---- new columns on users
    with op.batch_alter_table(
        "users",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s"},
    ) as batch:
        batch.add_column(sa.Column("contact_handle", sa.String(128), nullable=True))
        batch.add_column(sa.Column("paid_until", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("encrypted_seed", sa.LargeBinary, nullable=True))
        batch.add_column(sa.Column("seed_salt", sa.LargeBinary(16), nullable=True))
        batch.add_column(sa.Column("pin_hash", sa.String(255), nullable=True))
        batch.add_column(
            sa.Column(
                "pin_failed_attempts",
                sa.Integer,
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("pin_locked_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("pin_locked_until")
        batch.drop_column("pin_failed_attempts")
        batch.drop_column("pin_hash")
        batch.drop_column("seed_salt")
        batch.drop_column("encrypted_seed")
        batch.drop_column("paid_until")
        batch.drop_column("contact_handle")

    op.drop_index("ix_payment_requests_contact_handle", table_name="payment_requests")
    op.drop_index("ix_payment_requests_status", table_name="payment_requests")
    op.drop_table("payment_requests")
