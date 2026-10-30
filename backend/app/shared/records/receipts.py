"""Ingredient receipts that raised stock (UC-03 7a; architecture §5.3 [R12]).

One row per ingredient on a delivery: a multi-item invoice has a row for each
item, so the source key is (``source_type``, ``source_reference``,
``item_id``). The unique key and the shared guard [R1] together make a
repeated receipt raise stock only once. Rows never change once saved.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.records._common import Quantity, RecordMixin, Timestamp, new_id, utcnow


class InventoryReceipt(RecordMixin, Base):
    __tablename__ = "inventory_receipts"
    __record_type__ = "InventoryReceipt"
    __record_id__ = "receipt_id"
    __append_only__ = True
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        UniqueConstraint(
            "source_type", "source_reference", "item_id", name="uq_inventory_receipts_source"
        ),
    )

    receipt_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.item_id"), index=True)
    # In the inventory item's stock unit.
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    source_type: Mapped[str] = mapped_column(String(32))
    source_reference: Mapped[str] = mapped_column(String(200))
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"))
    received_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
