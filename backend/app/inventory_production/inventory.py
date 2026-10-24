"""Ingredient requirements and the shortage-only shopping list (BAKE-12, UC-03).

``InventoryService`` works out what one order needs from its items, the
owner-approved recipes and their per-batch ingredient quantities, compares the
needs with stock and saves a ``ShoppingList`` for the current order version
(architecture ``TX-STOCK-CHECK``). No AI is involved, and the check never
changes stock or reserves anything (UC-03; T-UC03).

Calculation (Stage 1 Fig. 10): an item needs
``quantity / recipe.yield_units * quantity_per_batch`` of each ingredient, in
the stock unit. 24 vanilla cupcakes from a 12-cupcake recipe with 300 g flour
per batch need 600 g. Needs are exact fractions, summed exactly over all items
(so twelve lines of one need the same as one line of twelve), then rounded
**up** once to the 0.001 stock precision, so a list never under-states what to
buy.

Availability (BAKE-19, IP-02) is the stock on hand less what released plans
commit to other active orders. A commitment is the order's line in the
``requirements_snapshot`` of its schedule's effective ``released_version``, so
there are no separate reservation records; a revision counts only once it is
released, and then replaces the version before it. Each order counts once,
however many tasks it has, and stops counting once it is completed (deducted)
or cancelled. The orders being checked are left out of the commitments, so a
re-check replaces their earlier need instead of adding to it. Guarded saves
(plan drafts and releases) recheck a whole plan with ``check_plan`` inside
``store.constraint_guard()``.

Problems (UC-03 1a-2a) are collected and reported together; when there is any,
no shopping list is saved, so nothing looks complete that is not.

Recipe choice (agreed with BAKE-11): an item's ``resolved_recipe_id`` when set,
otherwise the product's single recipe, unless the item has a customisation or
the order has allergen concerns. Those need an owner-approved recipe first and
never fall back to the standard recipe silently.

Workflow (architecture §4.1.1, ``ORDER_INVENTORY_CHECK_REQUESTED``): the check
runs right after a customer-confirmed draft commits (``check_after_draft_saved``)
and the owner can run it again with ``run_stock_check``. It is a separate
transaction (``TX-STOCK-CHECK``), so a failed check never undoes the draft.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
import math
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction

from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.shared import RecordRef, ValidatedRecordStore
from app.shared.records import (
    InventoryItem,
    Order,
    OrderItem,
    OrderStatus,
    ProductionSchedule,
    ProductionScheduleVersion,
    Recipe,
    ShoppingList,
)
from app.shared.store import StoreError

STOCK_ACTION = "inventory.stock_check"
_log = logging.getLogger(__name__)
# Stock quantities are kept to 0.001 (NUMERIC(12,3)).
_THOUSANDTHS = 1000

# Unit conversions the recipe may use: (recipe unit, stock unit) -> factor.
_CONVERSIONS: dict[tuple[str, str], Decimal] = {
    ("kg", "g"): Decimal(1000),
    ("g", "kg"): Decimal("0.001"),
    ("l", "ml"): Decimal(1000),
    ("ml", "l"): Decimal("0.001"),
}
# Item customisation text that does not change the recipe (Fig. 10 "standard").
_STANDARD = frozenset({"", "standard"})
_CLOSED = frozenset({OrderStatus.CANCELLED, OrderStatus.COMPLETED})


class ProblemKind(StrEnum):
    ORDER_CLOSED = "ORDER_CLOSED"
    NO_ITEMS = "NO_ITEMS"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    MISSING_RECIPE = "MISSING_RECIPE"
    RECIPE_MISMATCH = "RECIPE_MISMATCH"
    UNRESOLVED_CUSTOMISATION = "UNRESOLVED_CUSTOMISATION"
    ALLERGEN_REVIEW_REQUIRED = "ALLERGEN_REVIEW_REQUIRED"
    EMPTY_RECIPE = "EMPTY_RECIPE"
    INCOMPATIBLE_UNIT = "INCOMPATIBLE_UNIT"


@dataclass(frozen=True)
class Problem:
    kind: ProblemKind
    message: str
    order_item_id: uuid.UUID | None = None


class StockCheckError(StoreError):
    """The order cannot be checked as it stands; nothing was saved (UC-03 1a-2a)."""

    code = "VALIDATION_FAILED"

    def __init__(self, problems: list[Problem]):
        super().__init__("; ".join(problem.message for problem in problems))
        self.problems = problems


@dataclass(frozen=True)
class StockCheckResult:
    shopping_list_id: uuid.UUID
    order_ref: RecordRef
    # inventory item id -> quantity in the stock unit, three decimals.
    requirements: dict[uuid.UUID, Decimal]
    # Positive shortfalls only; empty when the stock covers the order (UC-03 4a).
    shortages: dict[uuid.UUID, Decimal]


class InventoryService:
    def __init__(self, store: ValidatedRecordStore) -> None:
        self._store = store

    def run_stock_check(
        self, order_id: uuid.UUID, *, expected_order_version: int, actor_id: uuid.UUID
    ) -> StockCheckResult:
        """UC-03 for one order version: calculate, check and save the list.

        Raises ``StaleVersionError`` if the order has moved past
        ``expected_order_version`` and ``StockCheckError`` for an order that
        cannot be checked; in both cases nothing is saved.
        """
        with self._store.unit_of_work() as session:
            # Lock the order for this short transaction: an edit waits until the
            # list is saved, so the list always matches the version it records.
            order = self._store.load(
                session, Order, order_id, version=expected_order_version, lock=True
            )
            if order.status in _CLOSED:
                raise StockCheckError(
                    [Problem(ProblemKind.ORDER_CLOSED, f"order is {order.status}")]
                )
            requirements = self.calculate_needs(session, order)
            shortages = self.check_stock(session, requirements, exclude_orders={order.order_id})
            shopping_list = ShoppingList(
                order_id=order.order_id,
                source_order_version=order.version,
                requirements=_to_json(requirements),
                shortages=_to_json(shortages),
            )
            self._store.commit(session, shopping_list, actor_id=actor_id, action=STOCK_ACTION)
            return StockCheckResult(
                shopping_list_id=shopping_list.list_id,
                order_ref=self._store.ref(order),
                requirements=requirements,
                shortages=shortages,
            )

    def check_after_draft_saved(
        self, order_id: uuid.UUID, *, order_version: int, actor_id: uuid.UUID
    ) -> StockCheckResult | None:
        """Run UC-03 straight after a draft commits; never undo or fail the draft.

        Returns ``None`` when the check could not be completed. The draft stays
        saved without a current shopping list, so pricing waits until the
        owner re-runs the check (architecture §4.1.1).
        """
        try:
            return self.run_stock_check(
                order_id, expected_order_version=order_version, actor_id=actor_id
            )
        except StockCheckError as exc:
            kinds = ", ".join(sorted({problem.kind for problem in exc.problems}))
            _log.warning("Stock check for order %s needs the owner: %s", order_id, kinds)
        except (StoreError, SQLAlchemyError) as exc:
            _log.warning("Stock check for order %s was not saved: %s", order_id, type(exc).__name__)
        return None

    def calculate_needs(self, session: Session, order: Order) -> dict[uuid.UUID, Decimal]:
        """UC-03 steps 1-2: what the whole order needs, per inventory item."""
        problems: list[Problem] = []
        if not order.items:
            problems.append(Problem(ProblemKind.NO_ITEMS, "order has no items"))
        totals: dict[uuid.UUID, Fraction] = defaultdict(Fraction)
        stock_units = dict(session.execute(select(InventoryItem.item_id, InventoryItem.unit)).all())

        for item in order.items:
            quantity = item.quantity
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                problems.append(
                    Problem(
                        ProblemKind.INVALID_QUANTITY,
                        f"quantity {quantity!r} is not a positive whole number",
                        item.order_item_id,
                    )
                )
                continue
            recipe = self._recipe_for(session, item, bool(order.allergen_concerns), problems)
            if recipe is None:
                continue
            if not recipe.ingredients:
                problems.append(
                    Problem(
                        ProblemKind.EMPTY_RECIPE, "recipe has no ingredients", item.order_item_id
                    )
                )
                continue
            for line in recipe.ingredients:
                stock_unit = stock_units[line.inventory_item_id]
                factor = conversion_factor(line.required_unit, stock_unit)
                if factor is None:
                    problems.append(
                        Problem(
                            ProblemKind.INCOMPATIBLE_UNIT,
                            f"recipe uses {line.required_unit!r}"
                            f" but stock is kept in {stock_unit!r}",
                            item.order_item_id,
                        )
                    )
                    continue
                totals[line.inventory_item_id] += ingredient_need(
                    quantity, recipe.yield_units, line.quantity_per_batch, factor
                )

        if problems:
            raise StockCheckError(problems)
        return {item_id: round_up_quantity(total) for item_id, total in totals.items()}

    def check_stock(
        self,
        session: Session,
        requirements: dict[uuid.UUID, Decimal],
        *,
        exclude_orders: Collection[uuid.UUID] = (),
    ) -> dict[uuid.UUID, Decimal]:
        """UC-03 steps 3-4: positive shortfalls against available stock (IP-02).

        ``exclude_orders`` are the orders ``requirements`` is for: their own
        commitments are replaced by it, not added to it.
        """
        available = self.available_stock(session, requirements, exclude_orders=exclude_orders)
        return {
            item_id: need - available[item_id]
            for item_id, need in requirements.items()
            if need > available[item_id]
        }

    def check_plan(
        self, session: Session, needs_by_order: Mapping[uuid.UUID, Mapping[uuid.UUID, Decimal]]
    ) -> dict[uuid.UUID, Decimal]:
        """IP-02 for a whole plan: its orders' combined needs, as positive shortfalls.

        Call it inside ``store.constraint_guard()`` in the guarded save, so a
        concurrent plan cannot be released on the same stock between the check
        and the save.
        """
        combined: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
        for needs in needs_by_order.values():
            for item_id, quantity in needs.items():
                combined[item_id] += quantity
        return self.check_stock(session, dict(combined), exclude_orders=needs_by_order.keys())

    def available_stock(
        self,
        session: Session,
        item_ids: Iterable[uuid.UUID],
        *,
        exclude_orders: Collection[uuid.UUID] = (),
    ) -> dict[uuid.UUID, Decimal]:
        """Stock on hand less commitments; negative if stock is over-committed."""
        on_hand = dict(
            session.execute(
                select(InventoryItem.item_id, InventoryItem.quantity_on_hand).where(
                    InventoryItem.item_id.in_(list(item_ids))
                )
            ).all()
        )
        committed = self.committed_needs(session, exclude_orders=exclude_orders)
        return {
            item_id: quantity - committed.get(item_id, Decimal(0))
            for item_id, quantity in on_hand.items()
        }

    def committed_needs(
        self, session: Session, *, exclude_orders: Collection[uuid.UUID] = ()
    ) -> dict[uuid.UUID, Decimal]:
        """Stock committed to active orders in released plans, per inventory item."""
        snapshots = session.scalars(
            select(ProductionScheduleVersion.requirements_snapshot).join(
                ProductionSchedule,
                and_(
                    ProductionSchedule.schedule_id == ProductionScheduleVersion.schedule_id,
                    ProductionSchedule.released_version == ProductionScheduleVersion.version,
                ),
            )
        )
        excluded = set(exclude_orders)
        by_order: dict[uuid.UUID, dict[uuid.UUID, Decimal]] = {}
        for snapshot in snapshots:
            for order_key, needs in snapshot.items():
                order_id = uuid.UUID(order_key)
                if order_id in excluded:
                    continue
                # Release keeps an order in one plan; if two ever hold it, count
                # it once at the larger need rather than twice.
                order_needs = by_order.setdefault(order_id, {})
                for item_key, quantity in needs.items():
                    item_id = uuid.UUID(item_key)
                    order_needs[item_id] = max(order_needs.get(item_id, Decimal(0)), Decimal(quantity))
        if not by_order:
            return {}
        # Completed orders have been deducted (TX-DEDUCT marks the order Completed
        # in the same transaction); cancelled ones no longer need stock.
        active = session.scalars(
            select(Order.order_id).where(
                Order.order_id.in_(list(by_order)), Order.status.not_in(list(_CLOSED))
            )
        )
        committed: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
        for order_id in active:
            for item_id, quantity in by_order[order_id].items():
                committed[item_id] += quantity
        return dict(committed)

    @staticmethod
    def _recipe_for(
        session: Session, item: OrderItem, allergen_concerns: bool, problems: list[Problem]
    ) -> Recipe | None:
        """The owner-approved recipe for an item (one recipe per product, §15)."""
        if item.resolved_recipe_id is not None:
            recipe = session.get(Recipe, item.resolved_recipe_id)
            if recipe is None or recipe.product_id != item.product_id:
                problems.append(
                    Problem(
                        ProblemKind.RECIPE_MISMATCH,
                        "resolved recipe is not this product's recipe",
                        item.order_item_id,
                    )
                )
                return None
            return recipe
        if allergen_concerns:
            # Stated allergen concerns are settled by the owner first (UC-01 3c).
            problems.append(
                Problem(
                    ProblemKind.ALLERGEN_REVIEW_REQUIRED,
                    "order has allergen concerns but no owner-approved recipe",
                    item.order_item_id,
                )
            )
            return None
        if (item.customisation or "").strip().lower() not in _STANDARD:
            # Ingredient-changing requests are settled by the owner first (UC-01 3b).
            problems.append(
                Problem(
                    ProblemKind.UNRESOLVED_CUSTOMISATION,
                    f"customisation {item.customisation!r} has no owner-approved recipe",
                    item.order_item_id,
                )
            )
            return None
        recipe = session.scalars(
            select(Recipe).where(Recipe.product_id == item.product_id)
        ).one_or_none()
        if recipe is None:
            problems.append(
                Problem(ProblemKind.MISSING_RECIPE, "product has no recipe", item.order_item_id)
            )
        return recipe


def conversion_factor(recipe_unit: str, stock_unit: str) -> Decimal | None:
    """Multiplier from the recipe unit to the stock unit, or None if incompatible."""
    if recipe_unit == stock_unit:
        return Decimal(1)
    return _CONVERSIONS.get((recipe_unit, stock_unit))


def ingredient_need(
    quantity: int, yield_units: int, per_batch: Decimal, factor: Decimal
) -> Fraction:
    """Exact need of one ingredient for one item, e.g. 24 / 12 * 300 g = 600 g.

    A fraction, not a rounded decimal, so needs can be summed without error.
    """
    return Fraction(quantity) * Fraction(per_batch) * Fraction(factor) / yield_units


def round_up_quantity(value: Fraction | Decimal) -> Decimal:
    """Round a need up to the 0.001 stock precision (never under-state it)."""
    thousandths = math.ceil(Fraction(value) * _THOUSANDTHS)
    return Decimal(thousandths).scaleb(-3)


def _to_json(quantities: dict[uuid.UUID, Decimal]) -> dict[str, str]:
    """``{inventory_item_id: "decimal quantity"}`` as stored in shopping_lists."""
    return {str(item_id): str(quantity) for item_id, quantity in quantities.items()}
