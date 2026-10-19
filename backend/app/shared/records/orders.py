"""Orders and their items (Stage 1 Fig. 6; §5.3).

A customer-confirmed summary is saved as a ``Draft`` order; ``Confirmed`` needs
an accepted, owner-approved quote (Stage 1 business invariant 1). That rule is
a service guard, not a column default.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.shared.records._common import (
    InPlaceVersioned,
    Money,
    Json,
    OrderStatus,
    Timestamp,
    new_id,
    one_of,
    utcnow,
)


class Order(InPlaceVersioned, Base):
    __tablename__ = "orders"
    __record_type__ = "Order"
    __record_id__ = "order_id"
    __table_args__ = (
        one_of("status", OrderStatus, "status"),
        CheckConstraint("collection_kind IN ('at', 'before')", name="collection_kind"),
        CheckConstraint("budget IS NULL OR budget >= 0", name="budget_nonnegative"),
        CheckConstraint(
            "(accepted_quote_id IS NULL) = (accepted_quote_version IS NULL)",
            name="accepted_quote_pair",
        ),
        CheckConstraint("version > 0", name="version_positive"),
        # quotes.order_id points back here, so this FK is added after both tables.
        ForeignKeyConstraint(
            ["accepted_quote_id", "accepted_quote_version"],
            ["quotes.quote_id", "quotes.version"],
            name="fk_orders_accepted_quote_quotes",
            use_alter=True,
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.customer_id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=OrderStatus.DRAFT)
    # Collection time (UC-01), stored in UTC.
    required_at: Mapped[datetime] = mapped_column(Timestamp)
    budget: Mapped[Decimal | None] = mapped_column(Money)
    # UC-01: preserve customer disclosures and collection deadline semantics.
    allergen_concerns: Mapped[list[str] | None] = mapped_column(Json, nullable=True)
    collection_kind: Mapped[str] = mapped_column(String(8), default="at")
    accepted_quote_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    accepted_quote_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)

    items: Mapped[list[OrderItem]] = relationship(
        cascade="all, delete-orphan", order_by="OrderItem.order_item_id"
    )


class OrderItem(Base):
    """Part of an order (composition); saved and audited with its order."""

    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    order_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.order_id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.product_id"))
    quantity: Mapped[int] = mapped_column(Integer)
    flavour: Mapped[str | None] = mapped_column(Text)
    theme: Mapped[str | None] = mapped_column(Text)
    customisation: Mapped[str | None] = mapped_column(Text)
    # [R10] The owner-approved recipe this item uses; OrderService sets it.
    resolved_recipe_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipes.recipe_id"))
