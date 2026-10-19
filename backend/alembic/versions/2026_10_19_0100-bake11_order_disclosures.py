"""BAKE-11 preserve allergen disclosure and collection deadline semantics.

Revision ID: bake11_disclosures
Revises: 06a90cacc254
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "bake11_disclosures"
down_revision = "06a90cacc254"
branch_labels = None
depends_on = None


def upgrade():
    # Existing orders have unknown disclosure, never silently interpreted as no concerns.
    op.add_column("orders", sa.Column("allergen_concerns", postgresql.JSONB(), nullable=True))
    op.add_column("orders", sa.Column("collection_kind", sa.String(8), nullable=False, server_default="at"))
    op.create_check_constraint("collection_kind", "orders", "collection_kind IN ('at', 'before')")


def downgrade():
    op.drop_constraint("collection_kind", "orders", type_="check")
    op.drop_column("orders", "collection_kind")
    op.drop_column("orders", "allergen_concerns")
