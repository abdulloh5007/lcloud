"""JSON document database tables.

Revision ID: 0006_json_db
Revises: 0005_sharing_versioning
Create Date: 2026-07-05

Adds the first LCloud DB storage layer:
- json_collections: per-user collection namespace
- json_documents: current materialized JSON document state
- json_operations: append-only oplog for replay and future Telegram JSONL segments
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_json_db"
down_revision: str | None = "0005_sharing_versioning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "json_collections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", name="fk_json_collections_user", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "name",
            name="uq_json_collections_owner_name",
        ),
    )
    op.create_index(
        "ix_json_collections_owner_user_id",
        "json_collections",
        ["owner_user_id"],
    )

    op.create_table(
        "json_documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "collection_id",
            sa.Integer,
            sa.ForeignKey(
                "json_collections.id",
                name="fk_json_documents_collection",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("doc_id", sa.String(128), nullable=False),
        sa.Column("data_json", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "collection_id",
            "doc_id",
            name="uq_json_documents_collection_doc",
        ),
    )
    op.create_index("ix_json_documents_collection_id", "json_documents", ["collection_id"])

    op.create_table(
        "json_operations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "collection_id",
            sa.Integer,
            sa.ForeignKey(
                "json_collections.id",
                name="fk_json_operations_collection",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("doc_id", sa.String(128), nullable=True),
        sa.Column("op", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_json_operations_collection_id", "json_operations", ["collection_id"])
    op.create_index("ix_json_operations_doc_id", "json_operations", ["doc_id"])
    op.create_index("ix_json_operations_created_at", "json_operations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_json_operations_created_at", table_name="json_operations")
    op.drop_index("ix_json_operations_doc_id", table_name="json_operations")
    op.drop_index("ix_json_operations_collection_id", table_name="json_operations")
    op.drop_table("json_operations")
    op.drop_index("ix_json_documents_collection_id", table_name="json_documents")
    op.drop_table("json_documents")
    op.drop_index("ix_json_collections_owner_user_id", table_name="json_collections")
    op.drop_table("json_collections")
