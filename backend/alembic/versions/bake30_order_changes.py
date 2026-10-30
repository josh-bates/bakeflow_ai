"""BAKE-30 customer-confirmed order changes and pending amendments."""
from alembic import op
import sqlalchemy as sa
revision = "bake30_order_changes"
down_revision = "bake33_deductions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("order_change_requests",
        sa.Column("change_id", sa.Uuid(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.order_id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("user_accounts.user_id"), nullable=False),
        sa.Column("expected_order_version", sa.Integer(), nullable=False),
        sa.Column("expected_state", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("proposed_terms", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("review_state", sa.String(24), nullable=False),
        sa.Column("owner_note", sa.Text()),
        sa.Column("quote_id", sa.Uuid()),
        sa.Column("quote_version", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("review_state IN ('Pending','AwaitingApproval','Quoted','Applied','Rejected','Withdrawn')", name="review_state"),
        sa.CheckConstraint("kind IN ('Change','Cancel','Refund')", name="kind"))
    op.create_index("ix_order_change_requests_order_id", "order_change_requests", ["order_id"])


def downgrade():
    op.drop_table("order_change_requests")
