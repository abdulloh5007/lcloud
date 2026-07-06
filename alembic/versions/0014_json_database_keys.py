"""Add public keys for JSON databases.

Revision ID: 0014_json_database_keys
Revises: 0013_json_databases
Create Date: 2026-07-06
"""

from __future__ import annotations

import secrets

import sqlalchemy as sa

from alembic import op

revision: str = "0014_json_database_keys"
down_revision: str | None = "0013_json_databases"
branch_labels: str | None = None
depends_on: str | None = None

ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def _new_database_key() -> str:
    return "lcdb_" + "".join(secrets.choice(ALPHABET) for _ in range(24))


def upgrade() -> None:
    with op.batch_alter_table("json_databases") as batch:
        batch.add_column(sa.Column("database_key", sa.String(64), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id FROM json_databases")).fetchall()
    used: set[str] = set()
    for (database_id,) in rows:
        key = _new_database_key()
        while key in used:
            key = _new_database_key()
        used.add(key)
        conn.execute(
            sa.text("UPDATE json_databases SET database_key = :key WHERE id = :id"),
            {"key": key, "id": database_id},
        )

    with op.batch_alter_table("json_databases", recreate="always") as batch:
        batch.alter_column("database_key", existing_type=sa.String(64), nullable=False)
        batch.create_index("ix_json_databases_database_key", ["database_key"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("json_databases", recreate="always") as batch:
        batch.drop_index("ix_json_databases_database_key")
        batch.drop_column("database_key")
