"""BAKE-32 ingredient receipts that raise stock once [R12].

Revision ID: bake32_receipts
Revises: bake14_sessions
"""
from alembic import op
import sqlalchemy as sa

revision = "bake32_receipts"
down_revision = "bake14_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("inventory_receipts",
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_reference", sa.String(length=200), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_inventory_receipts_quantity_positive")),
        sa.ForeignKeyConstraint(["actor_id"], ["user_accounts.user_id"],
                                name=op.f("fk_inventory_receipts_actor_id_user_accounts")),
        sa.ForeignKeyConstraint(["item_id"], ["inventory_items.item_id"],
                                name=op.f("fk_inventory_receipts_item_id_inventory_items")),
        sa.PrimaryKeyConstraint("receipt_id", name=op.f("pk_inventory_receipts")),
        sa.UniqueConstraint("source_type", "source_reference", "item_id",
                            name="uq_inventory_receipts_source"))
    op.create_index(op.f("ix_inventory_receipts_item_id"), "inventory_receipts", ["item_id"])


def downgrade():
    op.drop_index(op.f("ix_inventory_receipts_item_id"), table_name="inventory_receipts")
    op.drop_table("inventory_receipts")
