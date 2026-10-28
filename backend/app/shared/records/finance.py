"""Quotations, ledger transactions and mock payments (Stage 1 Fig. 6; §5.3).

Every quote revision is a new row (``quote_id``, ``version``), so earlier
proposals and their approvals stay in history [R7, R10]. Quotes carry no
approval status column: approval lives in ``approval_requests``.

``mock_payments`` (BAKE-39 [O3]) is a Stage 2 extension: a local simulation of
paying for a confirmed order, with no bank, card data or real provider.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.records._common import (
    CustomerDecision,
    InPlaceVersioned,
    Margin,
    Money,
    PaymentStatus,
    RecordMixin,
    RowVersioned,
    Timestamp,
    TransactionKind,
    new_id,
    one_of,
    utcnow,
)


class Quote(RowVersioned, Base):
    __tablename__ = "quotes"
    __record_type__ = "Quote"
    __record_id__ = "quote_id"
    # TX-ACCEPT records the customer's decision on the current version (§4.2);
    # every other change is a new version.
    __mutable_fields__ = frozenset({"customer_decision"})
    __table_args__ = (
        one_of("customer_decision", CustomerDecision, "customer_decision"),
        CheckConstraint(
            "material_cost >= 0 AND labour_cost >= 0 AND packaging_cost >= 0"
            " AND additional_cost >= 0 AND total >= 0",
            name="amounts_nonnegative",
        ),
        CheckConstraint("target_margin >= 0 AND target_margin < 1", name="margin_range"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    quote_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), index=True)
    # [R9] Set when the quote prices a pending UC-02 change (BAKE-30 adds the table).
    pending_change_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # [R10] The order version this quote was calculated from.
    source_order_version: Mapped[int] = mapped_column(Integer)
    material_cost: Mapped[Decimal] = mapped_column(Money)
    labour_cost: Mapped[Decimal] = mapped_column(Money)
    packaging_cost: Mapped[Decimal] = mapped_column(Money)
    additional_cost: Mapped[Decimal] = mapped_column(Money)
    target_margin: Mapped[Decimal] = mapped_column(Margin)
    total: Mapped[Decimal] = mapped_column(Money)
    customer_decision: Mapped[str] = mapped_column(String(16), default=CustomerDecision.PENDING)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class Transaction(RecordMixin, Base):
    """One income or expense entry (UC-06). Duplicates are rejected by source [R4]."""

    __tablename__ = "transactions"
    __record_type__ = "Transaction"
    __record_id__ = "transaction_id"
    __table_args__ = (
        one_of("kind", TransactionKind, "kind"),
        CheckConstraint("amount > 0", name="amount_positive"),
        UniqueConstraint("source_type", "source_reference", name="uq_transactions_source"),
        # NULL request keys never collide, so manual entries without one are allowed.
        UniqueConstraint("created_by", "request_key", name="uq_transactions_request_key"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.order_id"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.product_id"))
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Money)
    source_type: Mapped[str] = mapped_column(String(32))
    source_reference: Mapped[str] = mapped_column(String(200))
    request_key: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(Timestamp)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"))


class MockPayment(InPlaceVersioned, Base):
    """One local mock payment for a confirmed order (BAKE-39 [O3]).

    The amount is copied from the accepted quote when the payment is created
    and never comes from the client. A payment settles once: ``Succeeded``
    always has its one income transaction, ``Failed`` never has one.
    """

    __tablename__ = "mock_payments"
    __record_type__ = "MockPayment"
    __record_id__ = "payment_id"
    __table_args__ = (
        one_of("status", PaymentStatus, "status"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "(status = 'Succeeded') = (transaction_id IS NOT NULL)", name="income_iff_succeeded"
        ),
        CheckConstraint("(status = 'Created') = (processed_at IS NULL)", name="processed_iff_settled"),
        CheckConstraint("version > 0", name="version_positive"),
        ForeignKeyConstraint(
            ["accepted_quote_id", "accepted_quote_version"],
            ["quotes.quote_id", "quotes.version"],
            name="fk_mock_payments_accepted_quote_quotes",
        ),
        UniqueConstraint("transaction_id", name="uq_mock_payments_transaction_id"),
        # At most one open or paid payment per order; a failed one can be retried.
        Index(
            "uq_mock_payments_open_order",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('Created', 'Succeeded')"),
        ),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), index=True)
    accepted_quote_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    accepted_quote_version: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(16), default=PaymentStatus.CREATED)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transactions.transaction_id")
    )
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(Timestamp)
