"""BAKE-39 local mock payments for confirmed orders [O3].

Revision ID: bake39_payments
Revises: bake32_receipts
"""
from alembic import op
import sqlalchemy as sa

revision = "bake39_payments"
down_revision = "bake32_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mock_payments",
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_quote_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_quote_version", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('Created', 'Succeeded', 'Failed')", name=op.f("ck_mock_payments_status")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_mock_payments_amount_positive")),
        sa.CheckConstraint("(status = 'Succeeded') = (transaction_id IS NOT NULL)",
                           name=op.f("ck_mock_payments_income_iff_succeeded")),
        sa.CheckConstraint("(status = 'Created') = (processed_at IS NULL)",
                           name=op.f("ck_mock_payments_processed_iff_settled")),
        sa.CheckConstraint("version > 0", name=op.f("ck_mock_payments_version_positive")),
        sa.ForeignKeyConstraint(["accepted_quote_id", "accepted_quote_version"],
                                ["quotes.quote_id", "quotes.version"],
                                name="fk_mock_payments_accepted_quote_quotes"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"],
                                name=op.f("fk_mock_payments_order_id_orders")),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.transaction_id"],
                                name=op.f("fk_mock_payments_transaction_id_transactions")),
        sa.PrimaryKeyConstraint("payment_id", name=op.f("pk_mock_payments")),
        sa.UniqueConstraint("transaction_id", name="uq_mock_payments_transaction_id"))
    op.create_index(op.f("ix_mock_payments_order_id"), "mock_payments", ["order_id"])
    op.create_index("uq_mock_payments_open_order", "mock_payments", ["order_id"], unique=True,
                    postgresql_where=sa.text("status IN ('Created', 'Succeeded')"))


def downgrade():
    op.drop_index("uq_mock_payments_open_order", table_name="mock_payments")
    op.drop_index(op.f("ix_mock_payments_order_id"), table_name="mock_payments")
    op.drop_table("mock_payments")
