"""Products, recipes and ingredient stock (Stage 1 Fig. 7; §5.3).

One recipe per product (Stage 1 §15). Fig. 10: flour ``unit_cost = 0.003`` per
gram is stored at four decimals, never rounded to cents [R3].
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.shared.records._common import (
    InPlaceVersioned,
    Money,
    Quantity,
    RecordMixin,
    UnitCost,
    new_id,
)


class Product(RecordMixin, Base):
    __tablename__ = "products"
    __record_type__ = "Product"
    __record_id__ = "product_id"
    __table_args__ = (
        CheckConstraint("labour_minutes >= 0", name="labour_minutes_nonnegative"),
        CheckConstraint("packaging_cost >= 0", name="packaging_cost_nonnegative"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(64))
    labour_minutes: Mapped[Decimal] = mapped_column(Quantity)
    packaging_cost: Mapped[Decimal] = mapped_column(Money)


class Recipe(InPlaceVersioned, Base):
    __tablename__ = "recipes"
    __record_type__ = "Recipe"
    __record_id__ = "recipe_id"
    __table_args__ = (
        CheckConstraint("yield_units > 0", name="yield_positive"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    recipe_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.product_id"), unique=True)
    yield_units: Mapped[int] = mapped_column(Integer)

    ingredients: Mapped[list[RecipeIngredient]] = relationship(
        cascade="all, delete-orphan", order_by="RecipeIngredient.inventory_item_id"
    )


class RecipeIngredient(Base):
    """Part of a recipe (composition); not a record on its own."""

    __tablename__ = "recipe_ingredients"
    __table_args__ = (CheckConstraint("quantity_per_batch > 0", name="quantity_positive"),)

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recipes.recipe_id", ondelete="CASCADE"), primary_key=True
    )
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_items.item_id"), primary_key=True
    )
    quantity_per_batch: Mapped[Decimal] = mapped_column(Quantity)
    # Must equal the inventory item's unit; InventoryService rejects a mismatch.
    required_unit: Mapped[str] = mapped_column(String(16))


class InventoryItem(InPlaceVersioned, Base):
    __tablename__ = "inventory_items"
    __record_type__ = "InventoryItem"
    __record_id__ = "item_id"
    __table_args__ = (
        CheckConstraint("quantity_on_hand >= 0", name="on_hand_nonnegative"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_nonnegative"),
        CheckConstraint("reorder_threshold >= 0", name="threshold_nonnegative"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    ingredient_name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(16))
    quantity_on_hand: Mapped[Decimal] = mapped_column(Quantity)
    unit_cost: Mapped[Decimal] = mapped_column(UnitCost)
    # [O1] optional low-stock alert threshold.
    reorder_threshold: Mapped[Decimal] = mapped_column(Quantity, default=Decimal("0.000"))
