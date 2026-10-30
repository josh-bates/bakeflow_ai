"""BAKE-17 owner exception queue, following the current main migration head."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "bake17_referrals"
down_revision = "bake32_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "owner_referrals",
        sa.Column("referral_id", sa.Uuid(), primary_key=True),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers.customer_id"), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.order_id")),
        sa.Column("source_order_version", sa.Integer()),
        sa.Column("source_order_status", sa.String(16)),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column("conversation", postgresql.JSONB(), nullable=False),
        sa.Column("resolution", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("customer_id", "request_key", name="uq_referral_customer_key"),
        sa.CheckConstraint("kind IN ('Intake', 'Refund', 'InProductionChange')", name="kind"),
        sa.CheckConstraint("status IN ('Pending', 'NeedsClarification', 'AwaitingCustomer', 'Completed', 'Closed')", name="status"),
        sa.CheckConstraint("version > 0", name="version_positive"),
    )
    op.create_index("ix_owner_referrals_customer_id", "owner_referrals", ["customer_id"])


def downgrade():
    op.drop_table("owner_referrals")
