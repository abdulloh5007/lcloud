"""Add top-level JSON databases backed by Telegram clouds.

Revision ID: 0013_json_databases
Revises: 0012_app_auth
Create Date: 2026-07-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0013_json_databases"
down_revision: str | None = "0012_app_auth"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "json_databases",
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
            sa.ForeignKey("clouds.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_json_databases_owner_name"),
    )
    op.create_index("ix_json_databases_owner_user_id", "json_databases", ["owner_user_id"])

    # Every existing user receives a compatibility database. Reusing their
    # oldest cloud keeps existing media in place when possible.
    op.execute(
        sa.text(
            """
            INSERT INTO json_databases (owner_user_id, cloud_id, name, is_default)
            SELECT users.id,
                   COALESCE(
                       (SELECT storage_public_keys.cloud_id
                        FROM storage_public_keys
                        WHERE storage_public_keys.owner_user_id = users.id
                          AND storage_public_keys.revoked_at IS NULL
                        ORDER BY storage_public_keys.id
                        LIMIT 1),
                       (SELECT MIN(clouds.id) FROM clouds WHERE clouds.owner_user_id = users.id)
                   ),
                   'Legacy database',
                   1
            FROM users
            """
        )
    )

    with op.batch_alter_table("json_collections") as batch:
        batch.add_column(sa.Column("database_id", sa.Integer, nullable=True))
    op.execute(
        sa.text(
            "UPDATE json_collections SET database_id = "
            "(SELECT id FROM json_databases WHERE owner_user_id = json_collections.owner_user_id AND is_default = 1)"
        )
    )
    with op.batch_alter_table("json_collections", recreate="always") as batch:
        batch.alter_column("database_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_json_collections_database",
            "json_databases",
            ["database_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.drop_constraint("uq_json_collections_owner_name", type_="unique")
        batch.create_unique_constraint(
            "uq_json_collections_database_name", ["database_id", "name"]
        )
        batch.create_index("ix_json_collections_database_id", ["database_id"])

    _add_required_database_id("json_db_public_keys", "owner_user_id")
    _add_required_database_id("app_users", "project_owner_user_id")

    with op.batch_alter_table("app_users", recreate="always") as batch:
        batch.drop_constraint("uq_app_users_project_uid", type_="unique")
        batch.create_unique_constraint("uq_app_users_database_uid", ["database_id", "uid"])

    with op.batch_alter_table("storage_public_keys") as batch:
        batch.add_column(sa.Column("database_id", sa.Integer, nullable=True))
        batch.create_foreign_key(
            "fk_storage_public_keys_database",
            "json_databases",
            ["database_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_storage_public_keys_database_id", ["database_id"])
    op.execute(
        sa.text(
            "UPDATE storage_public_keys SET database_id = "
            "(SELECT id FROM json_databases "
            "WHERE owner_user_id = storage_public_keys.owner_user_id "
            "AND cloud_id = storage_public_keys.cloud_id AND is_default = 1)"
        )
    )

    op.create_table(
        "json_database_backup_state",
        sa.Column(
            "database_id",
            sa.Integer,
            sa.ForeignKey("json_databases.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("last_operation_id", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_json_database_backup_state_owner_user_id",
        "json_database_backup_state",
        ["owner_user_id"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO json_database_backup_state
                (database_id, owner_user_id, last_operation_id, updated_at)
            SELECT d.id, s.owner_user_id, s.last_operation_id, s.updated_at
            FROM json_backup_state s
            JOIN json_databases d ON d.owner_user_id = s.owner_user_id AND d.is_default = 1
            """
        )
    )

    with op.batch_alter_table("json_backup_segments") as batch:
        batch.add_column(sa.Column("database_id", sa.Integer, nullable=True))
    op.execute(
        sa.text(
            "UPDATE json_backup_segments SET database_id = "
            "(SELECT id FROM json_databases WHERE owner_user_id = json_backup_segments.owner_user_id AND is_default = 1)"
        )
    )
    with op.batch_alter_table("json_backup_segments", recreate="always") as batch:
        batch.alter_column("database_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_json_backup_segments_database",
            "json_databases",
            ["database_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_json_backup_segments_database_id", ["database_id"])
        batch.create_unique_constraint(
            "uq_json_backup_segments_database_range",
            ["database_id", "first_operation_id", "last_operation_id"],
        )


def _add_required_database_id(table: str, owner_column: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.add_column(sa.Column("database_id", sa.Integer, nullable=True))
    op.execute(
        sa.text(
            f"UPDATE {table} SET database_id = "
            f"(SELECT id FROM json_databases WHERE owner_user_id = {table}.{owner_column} AND is_default = 1)"
        )
    )
    with op.batch_alter_table(table, recreate="always") as batch:
        batch.alter_column("database_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            f"fk_{table}_database",
            "json_databases",
            ["database_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index(f"ix_{table}_database_id", ["database_id"])


def downgrade() -> None:
    raise RuntimeError("0013_json_databases is not safely reversible")
