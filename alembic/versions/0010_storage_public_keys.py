"""Add publishable storage keys.

Revision ID: 0010_storage_public_keys
Revises: 0009_json_db_public_keys
Create Date: 2026-07-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0010_storage_public_keys"
down_revision: str | None = "0009_json_db_public_keys"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "storage_public_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cloud_id",
            sa.Integer,
            sa.ForeignKey("clouds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(96), nullable=False, unique=True),
        sa.Column("prefix", sa.String(24), nullable=False),
        sa.Column("label", sa.String(64), nullable=False, server_default=""),
        sa.Column("allow_upload", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("allow_list", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("allow_download", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("allow_delete", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("max_file_bytes", sa.BigInteger, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_storage_public_keys_owner_user_id",
        "storage_public_keys",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_storage_public_keys_cloud_id",
        "storage_public_keys",
        ["cloud_id"],
    )
    op.create_index("ix_storage_public_keys_key", "storage_public_keys", ["key"])


def downgrade() -> None:
    op.drop_index("ix_storage_public_keys_key", table_name="storage_public_keys")
    op.drop_index("ix_storage_public_keys_cloud_id", table_name="storage_public_keys")
    op.drop_index(
        "ix_storage_public_keys_owner_user_id",
        table_name="storage_public_keys",
    )
    op.drop_table("storage_public_keys")
