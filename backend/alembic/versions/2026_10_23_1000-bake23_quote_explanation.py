"""BAKE-23 store the owner-facing quote explanation with each quote version.

The explanation (BAKE-22, UC-05 step 4) is saved in the same row as the
figures it explains, so a revised quote gets its own explanation. NULL means
no usable explanation, and the quote is then flagged for manual review (4a).

Revision ID: bake23_quote_explanation
Revises: bake14_sessions
"""
from alembic import op
import sqlalchemy as sa

revision = "bake23_quote_explanation"
down_revision = "bake14_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quotes", sa.Column("explanation", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("quotes", "explanation")
