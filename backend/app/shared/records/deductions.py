"""Order deductions: the once-only stock use of a completed order (IP-01; §5.3 [R2]).

When an order's last required task is completed, ``TX-DEDUCT`` saves one
``OrderDeduction`` with a line per ingredient, lowers ``quantity_on_hand`` by
those lines and marks the order Completed, all in one guarded transaction. The
unique ``order_id`` makes a repeated completion return the saved deduction
instead of lowering stock again. Rows never change once saved.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.shared.records._common import Quantity, RecordMixin, Timestamp, new_id, utcnow


class OrderDeduction(RecordMixin, Base):
    __tablename__ = "order_deductions"
    __record_type__ = "OrderDeduction"
    __record_id__ = "deduction_id"
    __append_only__ = True
    __table_args__ = (
        # The released version whose snapshot gave the quantities.
        ForeignKeyConstraint(
            ["schedule_id", "schedule_version"],
            ["production_schedule_versions.schedule_id", "production_schedule_versions.version"],
            name="fk_order_deductions_schedule_version",
        ),
    )

    deduction_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), unique=True)
    schedule_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    schedule_version: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"))
    deducted_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)

    lines: Mapped[list[OrderDeductionLine]] = relationship(
        cascade="all, delete-orphan", order_by="OrderDeductionLine.item_id"
    )


class OrderDeductionLine(Base):
    """One ingredient of a deduction (composition); saved and audited with it."""

    __tablename__ = "order_deduction_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    deduction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_deductions.deduction_id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.item_id"), primary_key=True)
    # In the inventory item's stock unit.
    quantity: Mapped[Decimal] = mapped_column(Quantity)
