"""BAKE-33 order deductions that lower stock once per order [R2].

Revision ID: bake33_deductions
Revises: bake32_receipts
"""
from alembic import op
import sqlalchemy as sa

revision = "bake33_deductions"
down_revision = "bake32_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("order_deductions",
        sa.Column("deduction_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("deducted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["user_accounts.user_id"],
                                name=op.f("fk_order_deductions_actor_id_user_accounts")),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"],
                                name=op.f("fk_order_deductions_order_id_orders")),
        sa.ForeignKeyConstraint(["schedule_id", "schedule_version"],
                                ["production_schedule_versions.schedule_id",
                                 "production_schedule_versions.version"],
                                name="fk_order_deductions_schedule_version"),
        sa.PrimaryKeyConstraint("deduction_id", name=op.f("pk_order_deductions")),
        sa.UniqueConstraint("order_id", name=op.f("uq_order_deductions_order_id")))
    op.create_table("order_deduction_lines",
        sa.Column("deduction_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_order_deduction_lines_quantity_positive")),
        sa.ForeignKeyConstraint(["deduction_id"], ["order_deductions.deduction_id"],
                                name=op.f("fk_order_deduction_lines_deduction_id_order_deductions"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["inventory_items.item_id"],
                                name=op.f("fk_order_deduction_lines_item_id_inventory_items")),
        sa.PrimaryKeyConstraint("deduction_id", "item_id", name=op.f("pk_order_deduction_lines")))


def downgrade():
    op.drop_table("order_deduction_lines")
    op.drop_table("order_deductions")
