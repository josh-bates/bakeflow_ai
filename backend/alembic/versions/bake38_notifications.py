"""BAKE-38 recorded transitions and in-app notification delivery.

Revision ID: bake38_notifications
Revises: bake33_deductions
"""
from alembic import op
import sqlalchemy as sa

revision = "bake38_notifications"
down_revision = "bake33_deductions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_transitions",
        sa.Column("transition_id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("event_kind", sa.String(64), nullable=False),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.order_id"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers.customer_id"), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), sa.ForeignKey("user_accounts.user_id"), nullable=False),
        sa.Column("order_version", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(16)),
        sa.Column("to_state", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("user_accounts.user_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflow_transitions_customer_id", "workflow_transitions", ["customer_id"])
    op.create_table(
        "notifications",
        sa.Column("notification_id", sa.Uuid(), primary_key=True),
        sa.Column("transition_id", sa.Uuid(), sa.ForeignKey("workflow_transitions.transition_id"), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), sa.ForeignKey("user_accounts.user_id"), nullable=False),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column("render_mode", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("transition_id", "recipient_id"),
    )


def downgrade():
    op.drop_table("notifications")
    op.drop_index("ix_workflow_transitions_customer_id", table_name="workflow_transitions")
    op.drop_table("workflow_transitions")
