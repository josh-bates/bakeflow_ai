"""BAKE-40: publications of approved content to a mock channel.

A content version is published at most once: the partial unique index allows
any number of failed attempts but only one claimed or published row per
(content_id, content_version), and an attempt claims its row before it posts,
so neither a retry nor an overlapping publish can double-post.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "bake40_mock_publications"
down_revision = "bake37_ai_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mock_publications",
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("content_id", sa.Uuid(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("publication_id"),
        sa.ForeignKeyConstraint(
            ["content_id", "content_version"],
            ["marketing_contents.content_id", "marketing_contents.version"],
            name="fk_mock_publications_content",
        ),
        sa.CheckConstraint("status IN ('Publishing', 'Published', 'Failed')", name="status"),
        sa.CheckConstraint(
            "(status = 'Published') = (reference IS NOT NULL)", name="reference_iff_published"
        ),
        sa.CheckConstraint(
            "(status = 'Published') = (published_at IS NOT NULL)", name="time_iff_published"
        ),
        sa.CheckConstraint("(status = 'Failed') = (error IS NOT NULL)", name="error_iff_failed"),
    )
    op.create_index(
        "ix_mock_publications_content_id", "mock_publications", ["content_id"]
    )
    op.create_index(
        "uq_mock_publications_published",
        "mock_publications",
        ["content_id", "content_version"],
        unique=True,
        postgresql_where=sa.text("status IN ('Publishing', 'Published')"),
    )


def downgrade() -> None:
    op.drop_index("uq_mock_publications_published", table_name="mock_publications")
    op.drop_index("ix_mock_publications_content_id", table_name="mock_publications")
    op.drop_table("mock_publications")
