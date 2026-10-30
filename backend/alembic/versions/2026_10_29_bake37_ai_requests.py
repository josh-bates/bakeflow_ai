"""BAKE-37 saved AI operations [R5].

Revision ID: bake37_ai_requests
Revises: bake33_deductions
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "bake37_ai_requests"
down_revision = "bake33_deductions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ai_requests",
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("task_kind", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("subject_version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=200), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 3",
                           name=op.f("ck_ai_requests_attempt_count_in_budget")),
        sa.CheckConstraint("latency_ms >= 0", name=op.f("ck_ai_requests_latency_nonnegative")),
        sa.CheckConstraint("status IN ('Running', 'Succeeded', 'Failed', 'Interrupted')",
                           name=op.f("ck_ai_requests_status")),
        sa.PrimaryKeyConstraint("request_id", name=op.f("pk_ai_requests")))
    op.create_index(op.f("ix_ai_requests_started_at"), "ai_requests", ["started_at"])


def downgrade():
    op.drop_index(op.f("ix_ai_requests_started_at"), table_name="ai_requests")
    op.drop_table("ai_requests")
