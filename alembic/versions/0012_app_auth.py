"""Add project end-user auth and document ownership.

Revision ID: 0012_app_auth
Revises: 0011_json_db_telegram_backup
Create Date: 2026-07-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0012_app_auth"
down_revision: str | None = "0011_json_db_telegram_backup"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "project_owner_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("uid", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="anonymous"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_owner_user_id", "uid", name="uq_app_users_project_uid"),
    )
    op.create_index("ix_app_users_project_owner_user_id", "app_users", ["project_owner_user_id"])

    op.create_table(
        "app_refresh_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "app_user_id",
            sa.Integer,
            sa.ForeignKey("app_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_app_refresh_sessions_app_user_id", "app_refresh_sessions", ["app_user_id"])
    op.create_index("ix_app_refresh_sessions_token_hash", "app_refresh_sessions", ["token_hash"])

    with op.batch_alter_table("json_documents") as batch:
        batch.add_column(sa.Column("owner_uid", sa.String(64), nullable=True))
        batch.create_index("ix_json_documents_owner_uid", ["owner_uid"])
    with op.batch_alter_table("json_operations") as batch:
        batch.add_column(sa.Column("owner_uid", sa.String(64), nullable=True))
        batch.create_index("ix_json_operations_owner_uid", ["owner_uid"])


def downgrade() -> None:
    with op.batch_alter_table("json_operations") as batch:
        batch.drop_index("ix_json_operations_owner_uid")
        batch.drop_column("owner_uid")
    with op.batch_alter_table("json_documents") as batch:
        batch.drop_index("ix_json_documents_owner_uid")
        batch.drop_column("owner_uid")
    op.drop_index("ix_app_refresh_sessions_token_hash", table_name="app_refresh_sessions")
    op.drop_index("ix_app_refresh_sessions_app_user_id", table_name="app_refresh_sessions")
    op.drop_table("app_refresh_sessions")
    op.drop_index("ix_app_users_project_owner_user_id", table_name="app_users")
    op.drop_table("app_users")
