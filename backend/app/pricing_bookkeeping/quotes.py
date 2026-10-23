"""Quote drafts, owner approval and customer acceptance (UC-05 steps 1-3, 5-9; BAKE-23).

**Waiting for BAKE-15.** Every approval step goes through ``QuoteApprovals``
(``approvals.py``), the interface Angelina's ``ApprovalService`` will
implement. Until it is merged, this flow is tested with a stand-in and is not
exposed over HTTP (routes and UI also need BAKE-14's sign-in).

The flow (architecture §4.1.2, §4.2):

1. ``draft`` / ``revise`` (TX-QUOTE-DRAFT, TX-QUOTE-REVISION): build the
   costing inputs from the order, its current-version shopping list (UC-03),
   ingredient unit costs and the owner's settings; calculate with
   ``FinanceService``; explain **outside** any transaction (BAKE-22); then
   save the quote version and its approval request in one unit of work.
2. ``approve`` / ``withhold``: the owner's decision on the exact version.
   Approving moves the order Draft -> Quoted without changing its version.
3. ``customer_view``: the customer sees only the latest version, and only
   once it is approved (6b, 7a).
4. ``accept`` (TX-ACCEPT): recheck that the version is current, approved and
   priced from the current order version, then record the acceptance and
   confirm the order in the **same** transaction (9, 9a). A retry of an
   acceptance that already succeeded returns it again; nothing is doubled.
5. ``reject``: record the decision for the owner (8a); the owner may revise.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.pricing_bookkeeping.approvals import QUOTE_APPROVAL_KIND, QuoteApprovals
from app.pricing_bookkeeping.quotation import (
    AdditionalCost,
    CostingInputs,
    FinanceService,
    IngredientRequirement,
    QuotationInputError,
    QuoteCalculation,
    QuoteLine,
)
from app.sales.orders import OrderService
from app.shared.records import (
    BakerySettings,
    Customer,
    CustomerDecision,
    InventoryItem,
    Order,
    OrderStatus,
    Product,
    Quote,
    ShoppingList,
)
from app.shared.refs import RecordRef
from app.shared.store import RecordNotFound, StaleVersionError, ValidatedRecordStore

DRAFT_ACTION = "quote.draft_saved"
REVISION_ACTION = "quote.revised"
DECISION_ACTION = "quote.customer_decision"

# Orders that can still be priced or re-priced.
_PRICEABLE = frozenset({OrderStatus.DRAFT, OrderStatus.QUOTED})


class QuoteError(Exception):
    """A quote step was refused; nothing was saved. ``code`` follows §6.2."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ExplanationOutcome:
    """What the BAKE-22 explainer returns: the checked text, or ``None`` (4a)."""

    text: str | None
    requires_manual_review: bool


# (calculation, units for a single-product order) -> outcome. BAKE-22's
# QuoteExplainer plugs in here; it runs outside any database transaction.
Explain = Callable[[QuoteCalculation, int | None], ExplanationOutcome]


@dataclass(frozen=True)
class QuoteView:
    """One quote version as the owner or the customer sees it."""

    ref: RecordRef
    order_id: uuid.UUID
    source_order_version: int
    material_cost: Decimal
    labour_cost: Decimal
    packaging_cost: Decimal
    additional_cost: Decimal
    target_margin: Decimal
    total: Decimal
    customer_decision: str
    requires_manual_review: bool
    explanation: str | None

    @classmethod
    def of(cls, quote: Quote) -> QuoteView:
        return cls(
            ref=RecordRef("Quote", quote.quote_id, quote.version),
            order_id=quote.order_id,
            source_order_version=quote.source_order_version,
            material_cost=quote.material_cost,
            labour_cost=quote.labour_cost,
            packaging_cost=quote.packaging_cost,
            additional_cost=quote.additional_cost,
            target_margin=quote.target_margin,
            total=quote.total,
            customer_decision=quote.customer_decision,
            requires_manual_review=quote.requires_manual_review,
            explanation=quote.explanation,
        )


@dataclass(frozen=True)
class Acceptance:
    """UC-05 step 9 result. ``replayed`` is True for a retry of a completed acceptance."""

    quote: RecordRef
    order: RecordRef
    replayed: bool = False


@dataclass(frozen=True)
class _Priced:
    order_version: int
    calculation: QuoteCalculation
    units: int | None


class QuoteService:
    def __init__(
        self,
        store: ValidatedRecordStore,
        approvals: QuoteApprovals,
        *,
        finance: FinanceService | None = None,
        orders: OrderService | None = None,
    ):
        self.store = store
        self.approvals = approvals
        self.finance = finance or FinanceService()
        self.orders = orders or OrderService(store)

    # 1. Draft and revise (owner) ---------------------------------------------

    def draft(
        self,
        order_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        additional_costs: Iterable[AdditionalCost] = (),
        explain: Explain | None = None,
    ) -> QuoteView:
        """Calculate, explain and save the first quote for an order, with its request."""
        additional_costs = tuple(additional_costs)
        with self.store.unit_of_work() as session:
            order = self._order(session, order_id)
            if self._latest_for_order(session, order_id) is not None:
                raise QuoteError("CONFLICT", "this order already has a quote; revise it instead")
            priced = self._price(session, order, additional_costs)
        explanation = _explain(explain, priced)
        with self.store.unit_of_work() as session:
            order = self._order(session, order_id, lock=True)
            self._require_unchanged(session, order, priced)
            # Re-checked under the order lock: two concurrent drafts make one quote.
            if self._latest_for_order(session, order_id) is not None:
                raise QuoteError("CONFLICT", "this order already has a quote; revise it instead")
            quote = _quote_row(order.order_id, priced, explanation)
            ref = self.store.commit(session, quote, actor_id=owner_id, action=DRAFT_ACTION)
            self.approvals.request(session, ref, kind=QUOTE_APPROVAL_KIND, actor_id=owner_id)
            return QuoteView.of(quote)

    def revise(
        self,
        quote: RecordRef,
        *,
        owner_id: uuid.UUID,
        additional_costs: Iterable[AdditionalCost] = (),
        explain: Explain | None = None,
    ) -> QuoteView:
        """Re-price as a new version (6a, 8a). Approval of the old version no longer applies."""
        additional_costs = tuple(additional_costs)
        with self.store.unit_of_work() as session:
            current = self._latest(session, quote.record_id)
            if current.version != quote.version:
                raise QuoteError("STALE_VERSION", f"quote is at version {current.version}")
            if current.customer_decision == CustomerDecision.ACCEPTED:
                raise QuoteError("CONFLICT", "an accepted quote cannot be revised")
            order = self._order(session, current.order_id)
            priced = self._price(session, order, additional_costs)
        explanation = _explain(explain, priced)
        with self.store.unit_of_work() as session:
            order = self._order(session, current.order_id, lock=True)
            self._require_unchanged(session, order, priced)
            revision = _quote_row(order.order_id, priced, explanation, quote_id=quote.record_id)
            try:
                ref = self.store.commit(
                    session, revision, actor_id=owner_id, action=REVISION_ACTION,
                    expected_version=quote.version,
                )
            except StaleVersionError:
                raise QuoteError("STALE_VERSION", "the quote was revised meanwhile") from None
            self.approvals.request(session, ref, kind=QUOTE_APPROVAL_KIND, actor_id=owner_id)
            return QuoteView.of(revision)

    # 2. Owner decision ----------------------------------------------------------

    def approve(self, quote: RecordRef, *, owner_id: uuid.UUID) -> QuoteView:
        """Approve exactly ``quote`` (step 6-7); the order moves Draft -> Quoted."""
        with self.store.unit_of_work() as session:
            current, order = self._current_for_owner(session, quote)
            self.approvals.decide(session, quote, approve=True, owner_id=owner_id)
            if order.status == OrderStatus.DRAFT:
                self.orders.mark_quoted(session, order, actor_id=owner_id)
            return QuoteView.of(current)

    def withhold(self, quote: RecordRef, *, owner_id: uuid.UUID) -> QuoteView:
        """Withhold approval (6b): the quote is not presented to the customer."""
        with self.store.unit_of_work() as session:
            current, _ = self._current_for_owner(session, quote)
            self.approvals.decide(session, quote, approve=False, owner_id=owner_id)
            return QuoteView.of(current)

    def latest(self, order_id: uuid.UUID) -> QuoteView | None:
        """The owner's view: the latest version of an order's quote, approved or not."""
        with self.store.unit_of_work() as session:
            quote = self._latest_for_order(session, order_id)
            return QuoteView.of(quote) if quote else None

    # 3-5. Customer ---------------------------------------------------------------

    def customer_view(self, order_id: uuid.UUID, *, customer_id: uuid.UUID) -> QuoteView | None:
        """The quote the customer may see: latest version, owner-approved, current."""
        with self.store.unit_of_work() as session:
            order = self._customer_order(session, order_id, customer_id)
            quote = self._latest_for_order(session, order_id)
            if quote is None or not self._presentable(session, quote, order):
                return None
            return QuoteView.of(quote)

    def accept(self, quote: RecordRef, *, customer_id: uuid.UUID) -> Acceptance:
        """TX-ACCEPT: record the acceptance and confirm the order together, or neither."""
        with self.store.unit_of_work() as session:
            current, order = self._for_customer_decision(session, quote, customer_id)
            if (
                order.status == OrderStatus.CONFIRMED
                and order.accepted_quote_id == quote.record_id
                and order.accepted_quote_version == quote.version
            ):
                return Acceptance(quote, self.store.ref(order), replayed=True)
            self._require_open_decision(session, current, order)
            current.customer_decision = CustomerDecision.ACCEPTED
            self.store.commit(
                session, current, actor_id=_customer_actor(session, order), action=DECISION_ACTION,
                expected_version=current.version,
            )
            order_ref = self.orders.confirm(
                session, order, quote, actor_id=_customer_actor(session, order)
            )
            return Acceptance(quote, order_ref)

    def reject(self, quote: RecordRef, *, customer_id: uuid.UUID) -> QuoteView:
        """Record the customer's rejection (8a); the order stays open for a revision."""
        with self.store.unit_of_work() as session:
            current, order = self._for_customer_decision(session, quote, customer_id)
            if current.customer_decision == CustomerDecision.REJECTED:
                return QuoteView.of(current)
            self._require_open_decision(session, current, order)
            current.customer_decision = CustomerDecision.REJECTED
            self.store.commit(
                session, current, actor_id=_customer_actor(session, order), action=DECISION_ACTION,
                expected_version=current.version,
            )
            return QuoteView.of(current)

    # Helpers ----------------------------------------------------------------------

    def _price(
        self, session: Session, order: Order, additional_costs: tuple[AdditionalCost, ...]
    ) -> _Priced:
        if order.status not in _PRICEABLE:
            raise QuoteError("CONFLICT", f"an order that is {order.status} cannot be priced")
        inputs = costing_inputs(session, order, additional_costs)
        try:
            calculation = self.finance.calculate_quote(inputs)
        except QuotationInputError as exc:
            raise QuoteError("VALIDATION_FAILED", str(exc)) from None
        units = inputs.lines[0].quantity if len(inputs.lines) == 1 else None
        return _Priced(order.version, calculation, units)

    def _require_unchanged(self, session: Session, order: Order, priced: _Priced) -> None:
        """The order must not have changed while the explanation was written."""
        if order.version != priced.order_version or order.status not in _PRICEABLE:
            raise QuoteError("STALE_VERSION", "the order changed while it was being priced")
        if _current_shopping_list(session, order) is None:
            raise QuoteError("STALE_VERSION", "the order's stock check is out of date")

    def _current_for_owner(self, session: Session, quote: RecordRef) -> tuple[Quote, Order]:
        current = self._latest(session, quote.record_id)
        if current.version != quote.version:
            raise QuoteError("STALE_VERSION", f"quote is at version {current.version}")
        order = self._order(session, current.order_id, lock=True)
        if current.source_order_version != order.version:
            raise QuoteError("STALE_VERSION", "the order changed after this quote was priced")
        return current, order

    def _for_customer_decision(
        self, session: Session, quote: RecordRef, customer_id: uuid.UUID
    ) -> tuple[Quote, Order]:
        current = self._latest(session, quote.record_id)
        order = self._customer_order(session, current.order_id, customer_id, lock=True)
        if current.version != quote.version:
            raise QuoteError(
                "STALE_VERSION", "this quote has been revised; a revised quotation will follow"
            )
        return current, order

    def _require_open_decision(self, session: Session, quote: Quote, order: Order) -> None:
        if not self._presentable(session, quote, order):
            raise QuoteError(
                "APPROVAL_REQUIRED", "this quote is not approved for the current order"
            )
        if quote.customer_decision != CustomerDecision.PENDING:
            raise QuoteError("CONFLICT", f"this quote was already {quote.customer_decision.lower()}")
        if order.status != OrderStatus.QUOTED:
            raise QuoteError("CONFLICT", f"an order that is {order.status} cannot accept a quote")

    def _presentable(self, session: Session, quote: Quote, order: Order) -> bool:
        ref = RecordRef("Quote", quote.quote_id, quote.version)
        return quote.source_order_version == order.version and self.approvals.is_approved(session, ref)

    def _order(self, session: Session, order_id: uuid.UUID, *, lock: bool = False) -> Order:
        try:
            return self.store.load(session, Order, order_id, lock=lock)
        except RecordNotFound:
            raise QuoteError("NOT_FOUND", "no such order") from None

    def _customer_order(
        self, session: Session, order_id: uuid.UUID, customer_id: uuid.UUID, *, lock: bool = False
    ) -> Order:
        order = self._order(session, order_id, lock=lock)
        if order.customer_id != customer_id:
            # Same answer as a missing order, so nothing is disclosed (UC-02 2a).
            raise QuoteError("NOT_FOUND", "no such order")
        return order

    def _latest(self, session: Session, quote_id: uuid.UUID) -> Quote:
        try:
            return self.store.load(session, Quote, quote_id)
        except RecordNotFound:
            raise QuoteError("NOT_FOUND", "no such quote") from None

    @staticmethod
    def _latest_for_order(session: Session, order_id: uuid.UUID) -> Quote | None:
        return session.scalars(
            select(Quote).where(Quote.order_id == order_id).order_by(Quote.version.desc()).limit(1)
        ).first()


def costing_inputs(
    session: Session, order: Order, additional_costs: tuple[AdditionalCost, ...] = ()
) -> CostingInputs:
    """UC-05 step 1: the order, its current-version requirements and the owner's rates."""
    shopping_list = _current_shopping_list(session, order)
    if shopping_list is None:
        raise QuoteError(
            "VALIDATION_FAILED",
            "the order needs a stock check for its current version before it can be priced",
        )
    products = {
        product.product_id: product
        for product in session.scalars(
            select(Product).where(Product.product_id.in_({item.product_id for item in order.items}))
        )
    }
    item_ids = [uuid.UUID(key) for key in shopping_list.requirements]
    unit_costs = dict(
        session.execute(
            select(InventoryItem.item_id, InventoryItem.unit_cost).where(InventoryItem.item_id.in_(item_ids))
        ).all()
    )
    settings = session.scalars(select(BakerySettings)).one_or_none()
    if settings is None:
        raise QuoteError("VALIDATION_FAILED", "the owner's labour rate and margin are not set")
    missing = [str(item_id) for item_id in item_ids if item_id not in unit_costs]
    if missing:
        raise QuoteError("VALIDATION_FAILED", f"unknown ingredients: {', '.join(missing)}")
    return CostingInputs(
        lines=tuple(
            QuoteLine(
                product_id=str(item.product_id),
                quantity=item.quantity,
                labour_minutes_per_unit=products[item.product_id].labour_minutes,
                packaging_cost_per_unit=products[item.product_id].packaging_cost,
            )
            for item in order.items
        ),
        ingredient_requirements=tuple(
            IngredientRequirement(str(item_id), Decimal(shopping_list.requirements[str(item_id)]),
                                  unit_costs[item_id])
            for item_id in item_ids
        ),
        labour_rate_per_hour=settings.labour_rate_per_hour,
        target_margin=settings.target_profit_margin,
        additional_costs=additional_costs,
    )


def _current_shopping_list(session: Session, order: Order) -> ShoppingList | None:
    """The newest UC-03 list for the order's current version (architecture §4.1.2 guard)."""
    return session.scalars(
        select(ShoppingList)
        .where(ShoppingList.order_id == order.order_id,
               ShoppingList.source_order_version == order.version)
        .order_by(ShoppingList.created_at.desc())
        .limit(1)
    ).first()


def _explain(explain: Explain | None, priced: _Priced) -> ExplanationOutcome:
    """Run the explainer outside any transaction. No explainer means no explanation (4a)."""
    if explain is None:
        return ExplanationOutcome(None, requires_manual_review=True)
    return explain(priced.calculation, priced.units)


def _quote_row(
    order_id: uuid.UUID,
    priced: _Priced,
    explanation: ExplanationOutcome,
    *,
    quote_id: uuid.UUID | None = None,
) -> Quote:
    calc = priced.calculation
    quote = Quote(
        order_id=order_id,
        source_order_version=priced.order_version,
        material_cost=calc.material_cost,
        labour_cost=calc.labour_cost,
        packaging_cost=calc.packaging_cost,
        additional_cost=calc.additional_cost,
        target_margin=calc.target_margin,
        total=calc.total,
        requires_manual_review=(
            calc.requires_manual_review
            or explanation.text is None
            or explanation.requires_manual_review
        ),
        explanation=explanation.text,
    )
    if quote_id is not None:
        quote.quote_id = quote_id
    return quote


def _customer_actor(session: Session, order: Order) -> uuid.UUID:
    """The customer's user account, the actor recorded for their decision."""
    return session.get(Customer, order.customer_id).user_id
