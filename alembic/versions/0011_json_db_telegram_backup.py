"""Add JSON DB Telegram backup state.

Revision ID: 0011_json_db_telegram_backup
Revises: 0010_storage_public_keys
Create Date: 2026-07-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0011_json_db_telegram_backup"
down_revision: str | None = "0010_storage_public_keys"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "json_backup_state",
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_operation_id", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "json_backup_segments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_operation_id", sa.Integer, nullable=False),
        sa.Column("last_operation_id", sa.Integer, nullable=False),
        sa.Column("operation_count", sa.Integer, nullable=False),
        sa.Column("telegram_chat", sa.String(64), nullable=False, server_default="me"),
        sa.Column("telegram_message_id", sa.BigInteger, nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="uploaded"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "owner_user_id",
            "first_operation_id",
            "last_operation_id",
            name="uq_json_backup_segments_owner_range",
        ),
    )
    op.create_index("ix_json_backup_segments_owner_user_id", "json_backup_segments", ["owner_user_id"])
    op.create_index("ix_json_backup_segments_first_operation_id", "json_backup_segments", ["first_operation_id"])
    op.create_index("ix_json_backup_segments_last_operation_id", "json_backup_segments", ["last_operation_id"])


def downgrade() -> None:
    op.drop_index("ix_json_backup_segments_last_operation_id", table_name="json_backup_segments")
    op.drop_index("ix_json_backup_segments_first_operation_id", table_name="json_backup_segments")
    op.drop_index("ix_json_backup_segments_owner_user_id", table_name="json_backup_segments")
    op.drop_table("json_backup_segments")
    op.drop_table("json_backup_state")
