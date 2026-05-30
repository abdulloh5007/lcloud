"""Add compression tracking columns to files.

Revision ID: 0003_compression
Revises: 0002_users_api_keys
Create Date: 2026-05-30

Adds two columns to `files`:

- `compressed BOOLEAN NOT NULL DEFAULT 0`
  True if the server re-encoded the file at upload time (lossy).
- `original_size_bytes INTEGER NULL`
  When `compressed=True`, the size of the file as the user originally
  uploaded it (before re-encoding). NULL means "same as size_bytes".

Existing rows: defaulted to compressed=False, original_size_bytes=NULL,
so legacy V1/V2 files keep behaving exactly as before.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_compression"
down_revision: str | None = "0002_users_api_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite needs batch_alter_table for safe ADD COLUMN with constraints.
    # We must also re-create the FTS5 triggers because batch_alter_table
    # drops + recreates the table.
    with op.batch_alter_table("files") as batch:
        batch.add_column(
            sa.Column(
                "compressed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(
            sa.Column("original_size_bytes", sa.BigInteger(), nullable=True)
        )

    # Re-create the FTS5 sync triggers (lost during batch table copy).
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
    with op.batch_alter_table("files") as batch:
        batch.drop_column("original_size_bytes")
        batch.drop_column("compressed")
