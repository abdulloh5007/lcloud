"""Sharing + versioning + monitoring scaffolding.

Revision ID: 0005_sharing_versioning
Revises: 0004_payments
Create Date: 2026-05-30

Adds:
- `file_shares` table — public share links for files (anonymous download)
- `files.replaces_file_id` — self-FK; when set, this file replaced the
  pointed-to one (file versioning). The replaced row gets `deleted_at`
  set so it disappears from normal listings but stays queryable for
  /api/v1/files/{id}/versions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_sharing_versioning"
down_revision: str | None = "0004_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- file_shares table
    op.create_table(
        "file_shares",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "file_id",
            sa.Integer,
            sa.ForeignKey("files.id", name="fk_share_file", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", name="fk_share_user"),
            nullable=False,
        ),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_downloads", sa.Integer, nullable=True),
        sa.Column("download_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_file_shares_token", "file_shares", ["token"])
    op.create_index("ix_file_shares_file_id", "file_shares", ["file_id"])
    op.create_index("ix_file_shares_owner_user_id", "file_shares", ["owner_user_id"])

    # ---- files.replaces_file_id (versioning)
    with op.batch_alter_table(
        "files",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s"},
    ) as batch:
        batch.add_column(
            sa.Column(
                "replaces_file_id",
                sa.Integer,
                sa.ForeignKey("files.id", name="fk_files_replaces"),
                nullable=True,
            )
        )

    # FTS triggers were preserved by previous migrations; re-create defensively
    # since batch_alter_table copies the table.
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

    op.create_index("ix_files_replaces_file_id", "files", ["replaces_file_id"])


def downgrade() -> None:
    op.drop_index("ix_files_replaces_file_id", table_name="files")
    with op.batch_alter_table("files") as batch:
        batch.drop_column("replaces_file_id")
    op.drop_index("ix_file_shares_owner_user_id", table_name="file_shares")
    op.drop_index("ix_file_shares_file_id", table_name="file_shares")
    op.drop_index("ix_file_shares_token", table_name="file_shares")
    op.drop_table("file_shares")
