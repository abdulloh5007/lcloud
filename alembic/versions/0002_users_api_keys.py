"""V2: users, api_keys, per-user owner columns.

Revision ID: 0002_users_api_keys
Revises: 0001_initial
Create Date: 2026-05-25

Schema additions:
    users        — registered identities, identified by Ed25519 pubkey.
                   No password column; auth is challenge-response via
                   client-side BIP39 → Ed25519 derived key.
    api_keys     — Bearer tokens minted per-user for programmatic access.
                   Stores argon2 hash of the raw key, not the key itself.
    challenges   — Optional, server-side nonces handed out on /auth/v2/challenge
                   and consumed on /auth/v2/verify. Provides replay protection.
    files.owner_user_id     — nullable FK; NULL = legacy admin file (V1).
    clouds.owner_user_id    — nullable FK; NULL = legacy admin cloud (V1).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_users_api_keys"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("pubkey", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_used_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "storage_quota_bytes",
            sa.BigInteger,
            nullable=False,
            # 5 GiB per user by default; admin gets a much higher cap below.
            server_default="5368709120",
        ),
        sa.Column("label", sa.String(64), nullable=True),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hash", sa.String(255), nullable=False),  # argon2 hash
        sa.Column("prefix", sa.String(16), nullable=False),  # first chars of raw key for UI display
        sa.Column("label", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])

    op.create_table(
        "auth_challenges",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("nonce", sa.String(64), nullable=False, unique=True),
        sa.Column("pubkey", sa.LargeBinary(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_challenges_nonce", "auth_challenges", ["nonce"])

    # Per-user ownership on existing data tables. NULL = legacy admin (V1).
    # SQLite needs batch_alter_table for adding FK columns; in batch mode,
    # all foreign keys must be explicitly named.
    with op.batch_alter_table(
        "clouds",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s"},
    ) as batch:
        batch.add_column(
            sa.Column(
                "owner_user_id",
                sa.Integer,
                sa.ForeignKey("users.id", name="fk_clouds_owner_user_id"),
                nullable=True,
            )
        )
    op.create_index("ix_clouds_owner_user_id", "clouds", ["owner_user_id"])

    with op.batch_alter_table(
        "files",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s"},
    ) as batch:
        batch.add_column(
            sa.Column(
                "owner_user_id",
                sa.Integer,
                sa.ForeignKey("users.id", name="fk_files_owner_user_id"),
                nullable=True,
            )
        )
    op.create_index("ix_files_owner_user_id", "files", ["owner_user_id"])

    # batch_alter_table on `files` recreates the table via copy+rename, which
    # drops triggers and FTS5 wiring. Re-create the FTS5 sync triggers
    # (originally added in 0001_initial).
    op.execute("DROP TRIGGER IF EXISTS files_fts_ai")
    op.execute("DROP TRIGGER IF EXISTS files_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS files_fts_au")
    op.execute(
        """
        CREATE TRIGGER files_fts_ai AFTER INSERT ON files BEGIN
            INSERT INTO files_fts(rowid, original_name)
                VALUES (new.id, new.original_name);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER files_fts_ad AFTER DELETE ON files BEGIN
            INSERT INTO files_fts(files_fts, rowid, original_name)
                VALUES ('delete', old.id, old.original_name);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER files_fts_au AFTER UPDATE ON files BEGIN
            INSERT INTO files_fts(files_fts, rowid, original_name)
                VALUES ('delete', old.id, old.original_name);
            INSERT INTO files_fts(rowid, original_name)
                VALUES (new.id, new.original_name);
        END
        """
    )


def downgrade() -> None:
    op.drop_index("ix_files_owner_user_id", table_name="files")
    with op.batch_alter_table("files") as batch:
        batch.drop_column("owner_user_id")
    op.drop_index("ix_clouds_owner_user_id", table_name="clouds")
    with op.batch_alter_table("clouds") as batch:
        batch.drop_column("owner_user_id")
    op.drop_index("ix_auth_challenges_nonce", table_name="auth_challenges")
    op.drop_table("auth_challenges")
    op.drop_index("ix_api_keys_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("users")
