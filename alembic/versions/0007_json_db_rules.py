"""Add access rules to JSON DB collections.

Revision ID: 0007_json_db_rules
Revises: 0006_json_db
Create Date: 2026-07-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0007_json_db_rules"
down_revision: str | None = "0006_json_db"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "json_collections",
        sa.Column("read_rule", sa.String(length=16), nullable=False, server_default="owner"),
    )
    op.add_column(
        "json_collections",
        sa.Column("write_rule", sa.String(length=16), nullable=False, server_default="owner"),
    )


def downgrade() -> None:
    op.drop_column("json_collections", "write_rule")
    op.drop_column("json_collections", "read_rule")
