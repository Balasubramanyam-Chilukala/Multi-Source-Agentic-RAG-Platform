"""Document sources, tenant settings, and heading-weighted full-text vectors.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24 09:40:44.601990

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

NEW_TSV = (
    "setweight(to_tsvector('english', coalesce(section, '')), 'A')"
    " || setweight(to_tsvector('english', text), 'B')"
)
OLD_TSV = "to_tsvector('english', text)"

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "document_sources",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=True),
        sa.Column("content", postgresql.BYTEA(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_sources_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("pk_document_sources")),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "settings", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
    )

    # Generated-column expressions cannot be altered in place: drop and re-create.
    op.drop_index("ix_chunks_tsv", table_name="chunks")
    op.drop_column("chunks", "tsv")
    op.add_column(
        "chunks",
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(NEW_TSV, persisted=True),
            nullable=False,
        ),
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_chunks_tsv", table_name="chunks")
    op.drop_column("chunks", "tsv")
    op.add_column(
        "chunks",
        sa.Column(
            "tsv", postgresql.TSVECTOR(), sa.Computed(OLD_TSV, persisted=True), nullable=False
        ),
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.drop_column("tenants", "settings")
    op.drop_table("document_sources")
