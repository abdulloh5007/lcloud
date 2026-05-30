"""Initial schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-24

Tables: owners, clouds, files, tags, file_tags, used_tokens, auth_state.
Plus FTS5 virtual table `files_fts` mirroring `files.original_name`,
kept in sync via AFTER INSERT/UPDATE/DELETE triggers.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("pubkey", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="admin"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "clouds",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("chat_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("owners.id"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("about", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_clouds_owner_id", "clouds", ["owner_id"])

    op.create_table(
        "files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cloud_id", sa.Integer, sa.ForeignKey("clouds.id"), nullable=False),
        sa.Column("message_id", sa.BigInteger, nullable=False),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("owners.id"), nullable=False),
        sa.Column("original_name", sa.String(512), nullable=False),
        sa.Column(
            "mime",
            sa.String(128),
            nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.LargeBinary(32), nullable=False),
        sa.Column("signature", sa.LargeBinary(64), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("cloud_id", "message_id", name="uq_files_cloud_msg"),
    )
    op.create_index("ix_files_cloud_id", "files", ["cloud_id"])
    op.create_index("ix_files_owner_id", "files", ["owner_id"])
    op.create_index("ix_files_uploaded_at", "files", ["uploaded_at"])

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("owners.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("color", sa.String(32), nullable=False),
        sa.Column("icon", sa.String(64), nullable=False),
        sa.Column("bg_color", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("owner_id", "name", name="uq_tags_owner_name"),
    )

    op.create_table(
        "file_tags",
        sa.Column(
            "file_id",
            sa.Integer,
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer,
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "used_tokens",
        sa.Column("jti", sa.String(64), primary_key=True),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "auth_state",
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("owners.id"),
            primary_key=True,
        ),
        sa.Column("epoch", sa.Integer, nullable=False, server_default="1"),
    )

    # FTS5 virtual table on files.original_name (+ sync triggers)
    op.execute(
        "CREATE VIRTUAL TABLE files_fts USING fts5("
        "original_name, content='files', content_rowid='id', tokenize='unicode61')"
    )
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
    op.execute("DROP TRIGGER IF EXISTS files_fts_au")
    op.execute("DROP TRIGGER IF EXISTS files_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS files_fts_ai")
    op.execute("DROP TABLE IF EXISTS files_fts")
    op.drop_table("auth_state")
    op.drop_table("used_tokens")
    op.drop_table("file_tags")
    op.drop_table("tags")
    op.drop_index("ix_files_uploaded_at", table_name="files")
    op.drop_index("ix_files_owner_id", table_name="files")
    op.drop_index("ix_files_cloud_id", table_name="files")
    op.drop_table("files")
    op.drop_index("ix_clouds_owner_id", table_name="clouds")
    op.drop_table("clouds")
    op.drop_table("owners")
