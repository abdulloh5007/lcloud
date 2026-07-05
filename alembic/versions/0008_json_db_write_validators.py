"""Add public write validators to JSON DB collections.

Revision ID: 0008_json_db_write_validators
Revises: 0007_json_db_rules
Create Date: 2026-07-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0008_json_db_write_validators"
down_revision: str | None = "0007_json_db_rules"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "json_collections",
        sa.Column("write_validator_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("json_collections", "write_validator_json")
