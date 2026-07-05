"""Add publishable DB keys.

Revision ID: 0009_json_db_public_keys
Revises: 0008_json_db_write_validators
Create Date: 2026-07-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0009_json_db_public_keys"
down_revision: str | None = "0008_json_db_write_validators"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "json_db_public_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(80), nullable=False, unique=True),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("label", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_json_db_public_keys_owner_user_id",
        "json_db_public_keys",
        ["owner_user_id"],
    )
    op.create_index("ix_json_db_public_keys_key", "json_db_public_keys", ["key"])


def downgrade() -> None:
    op.drop_index("ix_json_db_public_keys_key", table_name="json_db_public_keys")
    op.drop_index(
        "ix_json_db_public_keys_owner_user_id",
        table_name="json_db_public_keys",
    )
    op.drop_table("json_db_public_keys")
