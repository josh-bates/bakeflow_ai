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

Receipts (BAKE-32, UC-03 7a): ``receive_stock`` records one delivered
ingredient from an invoice or delivery note and raises ``quantity_on_hand`` in
the same guarded transaction (``TX-CONSTRAINT-EDIT``, [R1]), so the receipt and
the stock change are both audited. The source (type, reference, item) names
the delivery [R12]: repeating it returns the receipt already recorded without
raising stock again, and the same source with a different quantity is refused
(``CONFLICT``) rather than guessed at. References are trimmed and stored in
upper case, so ``inv-001`` and ``INV-001`` are the same invoice.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
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
    InventoryReceipt,
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
RECEIPT_ACTION = "inventory.receipt_recorded"
RECEIVE_ACTION = "inventory.stock_received"
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

# Where a delivered quantity comes from; part of the receipt's source key, so a
# fixed list keeps "invoice" and "Invoice" from counting as different sources.
RECEIPT_SOURCE_TYPES = frozenset({"Invoice", "DeliveryNote"})
MAX_REFERENCE_LENGTH = 200
# NUMERIC(12,3): stock and receipts stay below 10^9.
MAX_QUANTITY = Decimal("999999999.999")
_STOCK_PLACES = Decimal("0.001")


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


@dataclass(frozen=True)
class ReceiptInput:
    """One delivered ingredient the owner records (UC-03 7a)."""

    item_id: uuid.UUID
    # In the item's stock unit, at most three decimals.
    quantity: Decimal
    source_type: str
    source_reference: str


@dataclass(frozen=True)
class StockReceipt:
    """A recorded receipt. ``replayed`` is True when a repeat returned it."""

    receipt_id: uuid.UUID
    # The inventory item as stored now.
    item_ref: RecordRef
    ingredient: str
    unit: str
    quantity: Decimal
    source_type: str
    source_reference: str
    actor_id: uuid.UUID
    received_at: datetime
    on_hand: Decimal
    replayed: bool = False

    @classmethod
    def of(cls, row: InventoryReceipt, item: InventoryItem, *, replayed: bool = False) -> StockReceipt:
        return cls(
            receipt_id=row.receipt_id,
            item_ref=ValidatedRecordStore.ref(item),
            ingredient=item.ingredient_name,
            unit=item.unit,
            quantity=row.quantity,
            source_type=row.source_type,
            source_reference=row.source_reference,
            actor_id=row.actor_id,
            received_at=row.received_at,
            on_hand=item.quantity_on_hand,
            replayed=replayed,
        )


@dataclass(frozen=True)
class StockLevel:
    """One ingredient's stock: on hand, committed to released plans, and the rest."""

    item_id: uuid.UUID
    ingredient: str
    unit: str
    on_hand: Decimal
    committed: Decimal
    # Negative when released plans commit more than is on hand.
    available: Decimal


@dataclass(frozen=True)
class ReceiptIssue:
    field: str
    message: str


class ReceiptRejected(StoreError):
    """Invalid receipt details; nothing was recorded and stock is unchanged."""

    code = "VALIDATION_FAILED"

    def __init__(self, issues: tuple[ReceiptIssue, ...]):
        super().__init__("; ".join(f"{issue.field}: {issue.message}" for issue in issues))
        self.issues = issues


class ReceiptConflict(StoreError):
    """The source already recorded a different quantity of this item; stock is unchanged."""

    code = "CONFLICT"

    def __init__(self, existing: StockReceipt):
        super().__init__(
            f"{existing.source_type} {existing.source_reference} already recorded"
            f" {existing.quantity} {existing.unit} of {existing.ingredient}"
        )
        self.existing = existing


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
        self,
        session: Session,
        needs_by_order: Mapping[uuid.UUID, Mapping[uuid.UUID, Decimal]],
        *,
        exclude_orders: Collection[uuid.UUID] = (),
    ) -> dict[uuid.UUID, Decimal]:
        """IP-02 for a whole plan: its orders' combined needs, as positive shortfalls.

        ``exclude_orders`` are further orders whose commitments the plan
        replaces, such as those of the released version a revision replaces.
        Call it inside ``store.constraint_guard()`` in the guarded save, so a
        concurrent plan cannot be released on the same stock between the check
        and the save.
        """
        combined: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
        for needs in needs_by_order.values():
            for item_id, quantity in needs.items():
                combined[item_id] += quantity
        return self.check_stock(
            session, dict(combined), exclude_orders={*needs_by_order, *exclude_orders}
        )

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

    def receive_stock(self, receipt: ReceiptInput, *, actor_id: uuid.UUID) -> StockReceipt:
        """UC-03 7a: record one delivered ingredient and raise its stock once.

        Raises ``ReceiptRejected`` for invalid details, ``RecordNotFound`` for
        an unknown item and ``ReceiptConflict`` when the source already
        recorded a different quantity; in each case stock is unchanged.
        """
        issues = validate_receipt(receipt)
        if issues:
            raise ReceiptRejected(issues)
        receipt = _normalised_receipt(receipt)
        with self._store.constraint_guard() as session:
            # Under the guard, repeats of one receipt run one after another, so
            # a repeat always sees the receipt the first one saved.
            item = self._store.load(session, InventoryItem, receipt.item_id, lock=True)
            prior = session.scalars(
                select(InventoryReceipt).where(
                    InventoryReceipt.source_type == receipt.source_type,
                    InventoryReceipt.source_reference == receipt.source_reference,
                    InventoryReceipt.item_id == receipt.item_id,
                )
            ).one_or_none()
            if prior is not None:
                if prior.quantity != receipt.quantity:
                    raise ReceiptConflict(StockReceipt.of(prior, item))
                return StockReceipt.of(prior, item, replayed=True)
            on_hand = item.quantity_on_hand + receipt.quantity
            if on_hand > MAX_QUANTITY:
                raise ReceiptRejected(
                    (ReceiptIssue("quantity", f"would raise stock above {MAX_QUANTITY}"),)
                )
            row = InventoryReceipt(
                item_id=item.item_id,
                quantity=receipt.quantity,
                source_type=receipt.source_type,
                source_reference=receipt.source_reference,
                actor_id=actor_id,
            )
            self._store.commit(session, row, actor_id=actor_id, action=RECEIPT_ACTION)
            item.quantity_on_hand = on_hand
            self._store.commit(
                session, item, actor_id=actor_id, action=RECEIVE_ACTION,
                expected_version=item.version, revise=True,
            )
            return StockReceipt.of(row, item)

    def stock_levels(self) -> list[StockLevel]:
        """Every ingredient by name: on hand, committed (IP-02) and available.

        One snapshot: a deduction (TX-DEDUCT) lowers stock and completes its
        order together, so reading the two in separate states could show the
        old stock next to the released commitment, overstating what is left.
        """
        with self._store.snapshot() as session:
            items = session.scalars(
                select(InventoryItem).order_by(InventoryItem.ingredient_name, InventoryItem.item_id)
            ).all()
            committed = self.committed_needs(session)
            levels = []
            for item in items:
                need = committed.get(item.item_id, Decimal(0))
                levels.append(
                    StockLevel(
                        item_id=item.item_id,
                        ingredient=item.ingredient_name,
                        unit=item.unit,
                        on_hand=_stock(item.quantity_on_hand),
                        committed=_stock(need),
                        available=_stock(item.quantity_on_hand - need),
                    )
                )
            return levels

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


def validate_receipt(receipt: ReceiptInput) -> tuple[ReceiptIssue, ...]:
    """Every invalid field of ``receipt``; empty means valid."""
    issues: list[ReceiptIssue] = []
    if not isinstance(receipt.item_id, uuid.UUID):
        issues.append(ReceiptIssue("item_id", "must be a UUID"))
    quantity = receipt.quantity
    if not isinstance(quantity, Decimal):
        issues.append(ReceiptIssue("quantity", "must be a number, such as 2500 or 2.5"))
    elif not quantity.is_finite():
        issues.append(ReceiptIssue("quantity", "must be a finite number"))
    elif quantity <= 0:
        issues.append(ReceiptIssue("quantity", "must be positive"))
    elif quantity > MAX_QUANTITY:
        issues.append(ReceiptIssue("quantity", f"must be at most {MAX_QUANTITY}"))
    elif quantity != quantity.quantize(_STOCK_PLACES):
        issues.append(ReceiptIssue("quantity", "must have at most 3 decimal places"))
    if receipt.source_type not in RECEIPT_SOURCE_TYPES:
        issues.append(
            ReceiptIssue("source_type", f"must be one of {', '.join(sorted(RECEIPT_SOURCE_TYPES))}")
        )
    reference = receipt.source_reference
    if not isinstance(reference, str) or not reference.strip():
        issues.append(
            ReceiptIssue("source_reference", "is required, such as an invoice or delivery note number")
        )
    elif "\x00" in reference:
        issues.append(ReceiptIssue("source_reference", "contains a NUL character"))
    # Measured as stored: upper-casing can lengthen text ("ß" becomes "SS").
    elif len(reference.strip().upper()) > MAX_REFERENCE_LENGTH:
        issues.append(
            ReceiptIssue("source_reference", f"must be at most {MAX_REFERENCE_LENGTH} characters")
        )
    return tuple(issues)


def _normalised_receipt(receipt: ReceiptInput) -> ReceiptInput:
    """Three-decimal quantity; trimmed, upper-case reference."""
    return ReceiptInput(
        item_id=receipt.item_id,
        quantity=_stock(receipt.quantity),
        source_type=receipt.source_type,
        source_reference=receipt.source_reference.strip().upper(),
    )


def _stock(quantity: Decimal) -> Decimal:
    """A stock quantity at its stored 0.001 precision, e.g. ``0`` -> ``0.000``."""
    return quantity.quantize(_STOCK_PLACES)
