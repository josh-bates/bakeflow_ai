"""BAKE-14 persistent revocable sessions.

Revision ID: bake14_sessions
Revises: bake11_disclosures
"""
from alembic import op
import sqlalchemy as sa

revision = "bake14_sessions"
down_revision = "bake11_disclosures"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user_accounts.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade():
    op.drop_table("auth_sessions")
