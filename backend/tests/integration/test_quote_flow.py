"""BAKE-23: draft, approve and accept quotes (UC-05 steps 1-3, 5-9), on PostgreSQL.

Approvals go through BAKE-15's real ``ApprovalService``.

Jira tests: accepting a superseded version does not confirm the order; a
revised version cannot be sent without new approval; a simulated OrderService
failure after acceptance leaves no accepted quotation and no confirmed order,
and a retry then succeeds exactly once.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.inventory_production import InventoryService
from app.pricing_bookkeeping.quotation import AdditionalCost
from app.pricing_bookkeeping.quotes import (
    DECISION_ACTION,
    ExplanationOutcome,
    QuoteError,
    QuoteService,
)
from app.sales.orders import OrderService
from app.shared.approvals import ApprovalKind
from app.shared.records import (
    ApprovalDecision,
    ApprovalRequest,
    AuditEntry,
    CustomerDecision,
    Order,
    OrderItem,
    OrderStatus,
    Quote,
)
from app.shared.refs import RecordRef

REQUIRED_AT = datetime.now(UTC) + timedelta(days=14)


def consistent_explanation(calc, units):
    return ExplanationOutcome(f"The quoted price is ${calc.total}.", requires_manual_review=False)


@pytest.fixture
def quotes(store) -> QuoteService:
    return QuoteService(store)


def priced_order(store, seeded, cupcakes=24) -> uuid.UUID:
    """A customer's Draft order with its UC-03 stock check done."""
    with store.unit_of_work() as session:
        order = Order(
            customer_id=seeded.customer_id, required_at=REQUIRED_AT, allergen_concerns=[],
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=cupcakes,
                             customisation="standard")],
        )
        ref = store.commit(session, order, actor_id=seeded.customer_user_id, action="order.draft")
    InventoryService(store).run_stock_check(ref.record_id, expected_order_version=ref.version,
                                            actor_id=seeded.owner_id)
    return ref.record_id


def load(store, model, record_id):
    with store.unit_of_work() as session:
        return store.load(session, model, record_id)


def count(store, model, *where):
    with store.unit_of_work() as session:
        return session.scalar(select(func.count()).select_from(model).where(*where))


# 1. Drafting (TX-QUOTE-DRAFT) --------------------------------------------------


def test_draft_prices_the_seeded_order_and_requests_approval(store, seeded, quotes):
    order_id = priced_order(store, seeded)

    view = quotes.draft(order_id, owner_id=seeded.owner_id, explain=consistent_explanation)

    # 24 vanilla cupcakes from the seed, worked by hand:
    # ingredients 1.80+0.80+3.60+2.40+0.36+4.00+2.00 = 14.96; labour 90 min at 30.00/h
    # = 45.00; packaging 24 x 0.25 = 6.00; 65.96 / (1 - 0.30) = 94.228... -> 94.23.
    assert (view.material_cost, view.labour_cost, view.packaging_cost) == (
        Decimal("14.96"), Decimal("45.00"), Decimal("6.00"))
    assert view.total == Decimal("94.23")
    assert view.ref.version == 1
    assert view.source_order_version == 1
    assert view.explanation == "The quoted price is $94.23."
    assert not view.requires_manual_review
    assert count(store, ApprovalRequest, ApprovalRequest.subject_id == view.ref.record_id) == 1


def test_no_usable_explanation_flags_manual_review(store, seeded, quotes):
    order_id = priced_order(store, seeded)
    view = quotes.draft(order_id, owner_id=seeded.owner_id)  # no explainer (4a)
    assert view.explanation is None
    assert view.requires_manual_review
    assert view.total == Decimal("94.23")


def test_additional_costs_are_included(store, seeded, quotes):
    order_id = priced_order(store, seeded)
    view = quotes.draft(order_id, owner_id=seeded.owner_id,
                        additional_costs=[AdditionalCost("Gift box", Decimal("4.50"))])
    assert view.additional_cost == Decimal("4.50")


def test_order_without_a_current_stock_check_is_not_priced(store, seeded, quotes):
    with store.unit_of_work() as session:
        order = Order(customer_id=seeded.customer_id, required_at=REQUIRED_AT, allergen_concerns=[],
                      items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=24)])
        ref = store.commit(session, order, actor_id=seeded.customer_user_id, action="order.draft")

    with pytest.raises(QuoteError) as error:
        quotes.draft(ref.record_id, owner_id=seeded.owner_id)
    assert error.value.code == "VALIDATION_FAILED"
    assert count(store, Quote) == 0


def test_draft_and_request_are_saved_together_or_not_at_all(store, seeded, quotes, monkeypatch):
    order_id = priced_order(store, seeded)

    def request_fails(*args, **kwargs):
        raise RuntimeError("simulated approval request failure")

    monkeypatch.setattr(quotes.approvals, "request", request_fails)
    with pytest.raises(RuntimeError):
        quotes.draft(order_id, owner_id=seeded.owner_id)
    assert count(store, Quote) == 0


def test_an_order_gets_one_quote_then_revisions(store, seeded, quotes):
    order_id = priced_order(store, seeded)
    quotes.draft(order_id, owner_id=seeded.owner_id)
    with pytest.raises(QuoteError) as error:
        quotes.draft(order_id, owner_id=seeded.owner_id)
    assert error.value.code == "CONFLICT"


# 2. Owner decision -----------------------------------------------------------------


def test_customer_sees_only_an_approved_quote(store, seeded, quotes):
    order_id = priced_order(store, seeded)
    view = quotes.draft(order_id, owner_id=seeded.owner_id)
    assert quotes.customer_view(order_id, customer_id=seeded.customer_id) is None

    quotes.withhold(view.ref, owner_id=seeded.owner_id)  # 6b
    assert quotes.customer_view(order_id, customer_id=seeded.customer_id) is None

    # A withheld version stays withheld; the owner revises it instead.
    with pytest.raises(QuoteError) as refused:
        quotes.approve(view.ref, owner_id=seeded.owner_id)
    assert refused.value.code == "CONFLICT"
    v2 = quotes.revise(view.ref, owner_id=seeded.owner_id)
    quotes.approve(v2.ref, owner_id=seeded.owner_id)
    assert quotes.customer_view(order_id, customer_id=seeded.customer_id).ref == v2.ref


def test_approval_is_recorded_once_by_approval_service(store, seeded, quotes):
    order_id = priced_order(store, seeded)
    view = quotes.draft(order_id, owner_id=seeded.owner_id)
    quotes.approve(view.ref, owner_id=seeded.owner_id)
    quotes.approve(view.ref, owner_id=seeded.owner_id)  # a double click changes nothing

    with store.unit_of_work() as session:
        request = session.scalars(
            select(ApprovalRequest).where(ApprovalRequest.subject_id == view.ref.record_id)
        ).one()
    assert request.kind == ApprovalKind.QUOTATION
    assert request.subject_version == view.ref.version
    assert request.decision == ApprovalDecision.APPROVED
    assert request.decided_by == seeded.owner_id
    assert count(store, AuditEntry, AuditEntry.action == "approval.approved") == 1


def test_approval_moves_the_order_to_quoted_without_changing_its_version(store, seeded, quotes):
    order_id = priced_order(store, seeded)
    view = quotes.draft(order_id, owner_id=seeded.owner_id)
    quotes.approve(view.ref, owner_id=seeded.owner_id)

    order = load(store, Order, order_id)
    assert order.status == OrderStatus.QUOTED
    assert order.version == view.source_order_version == 1


def test_approving_a_superseded_version_is_refused(store, seeded, quotes):
    order_id = priced_order(store, seeded)
    v1 = quotes.draft(order_id, owner_id=seeded.owner_id)
    quotes.revise(v1.ref, owner_id=seeded.owner_id)

    with pytest.raises(QuoteError) as error:
        quotes.approve(v1.ref, owner_id=seeded.owner_id)
    assert error.value.code == "STALE_VERSION"


def test_revision_committed_while_approval_waits_for_the_lock_is_not_approved(
        store, seeded, quotes, monkeypatch):
    """Review of #21: approve locks the order before it reads the latest version."""
    order_id = priced_order(store, seeded)
    v1 = quotes.draft(order_id, owner_id=seeded.owner_id)
    original_order = QuoteService._order
    revised = []
    triggered = []

    def revise_just_before_the_lock(self, session, order_id_, *, lock=False):
        if lock and not triggered:
            triggered.append(True)  # revise() locks the order too; trigger only once
            revised.append(quotes.revise(v1.ref, owner_id=seeded.owner_id))  # another request
        return original_order(self, session, order_id_, lock=lock)

    monkeypatch.setattr(QuoteService, "_order", revise_just_before_the_lock)
    with pytest.raises(QuoteError) as error:
        quotes.approve(v1.ref, owner_id=seeded.owner_id)

    assert error.value.code == "STALE_VERSION"
    assert revised[0].ref.version == 2
    assert load(store, Order, order_id).status == OrderStatus.DRAFT


def test_revised_version_is_not_sent_without_new_approval(store, seeded, quotes):
    order_id = priced_order(store, seeded)
    v1 = quotes.draft(order_id, owner_id=seeded.owner_id)
    quotes.approve(v1.ref, owner_id=seeded.owner_id)

    v2 = quotes.revise(v1.ref, owner_id=seeded.owner_id,
                       additional_costs=[AdditionalCost("Custom topper", Decimal("7.00"))])
    assert v2.ref.version == 2
    assert quotes.customer_view(order_id, customer_id=seeded.customer_id) is None  # 7a

    quotes.approve(v2.ref, owner_id=seeded.owner_id)
    assert quotes.customer_view(order_id, customer_id=seeded.customer_id).ref == v2.ref


# 3-5. Customer decision (TX-ACCEPT) ------------------------------------------------


def approved_quote(store, seeded, quotes) -> tuple[uuid.UUID, RecordRef]:
    order_id = priced_order(store, seeded)
    view = quotes.draft(order_id, owner_id=seeded.owner_id)
    quotes.approve(view.ref, owner_id=seeded.owner_id)
    return order_id, view.ref


def test_accepting_the_current_approved_quote_confirms_the_order(store, seeded, quotes):
    order_id, ref = approved_quote(store, seeded, quotes)

    accepted = quotes.accept(ref, customer_id=seeded.customer_id)

    assert not accepted.replayed
    order = load(store, Order, order_id)
    assert order.status == OrderStatus.CONFIRMED
    assert (order.accepted_quote_id, order.accepted_quote_version) == (ref.record_id, ref.version)
    assert load(store, Quote, ref.record_id).customer_decision == CustomerDecision.ACCEPTED


def test_accepting_a_superseded_version_does_not_confirm_the_order(store, seeded, quotes):
    order_id, v1 = approved_quote(store, seeded, quotes)
    quotes.revise(v1, owner_id=seeded.owner_id)

    with pytest.raises(QuoteError) as error:
        quotes.accept(v1, customer_id=seeded.customer_id)

    assert error.value.code == "STALE_VERSION"
    assert load(store, Order, order_id).status == OrderStatus.QUOTED


def test_unapproved_quote_cannot_be_accepted(store, seeded, quotes):
    order_id = priced_order(store, seeded)
    view = quotes.draft(order_id, owner_id=seeded.owner_id)
    with pytest.raises(QuoteError) as error:
        quotes.accept(view.ref, customer_id=seeded.customer_id)
    assert error.value.code == "APPROVAL_REQUIRED"


def test_failed_confirmation_stores_neither_acceptance_nor_confirmation(store, seeded, quotes, monkeypatch):
    order_id, ref = approved_quote(store, seeded, quotes)

    original = OrderService.confirm

    def confirm_fails(self, *args, **kwargs):
        raise RuntimeError("simulated OrderService failure")

    monkeypatch.setattr(OrderService, "confirm", confirm_fails)
    with pytest.raises(RuntimeError):
        quotes.accept(ref, customer_id=seeded.customer_id)

    assert load(store, Quote, ref.record_id).customer_decision == CustomerDecision.PENDING
    assert load(store, Order, order_id).status == OrderStatus.QUOTED

    monkeypatch.setattr(OrderService, "confirm", original)
    first = quotes.accept(ref, customer_id=seeded.customer_id)
    retry = quotes.accept(ref, customer_id=seeded.customer_id)

    assert not first.replayed and retry.replayed
    assert load(store, Order, order_id).status == OrderStatus.CONFIRMED
    # Exactly one acceptance and one confirmation were recorded.
    assert count(store, AuditEntry, AuditEntry.action == DECISION_ACTION) == 1
    assert count(store, AuditEntry, AuditEntry.action == "order.confirmed") == 1


def test_rejection_is_recorded_and_the_owner_can_revise(store, seeded, quotes):
    order_id, ref = approved_quote(store, seeded, quotes)

    rejected = quotes.reject(ref, customer_id=seeded.customer_id)

    assert rejected.customer_decision == CustomerDecision.REJECTED
    assert quotes.latest(order_id).customer_decision == CustomerDecision.REJECTED  # owner sees it
    assert load(store, Order, order_id).status == OrderStatus.QUOTED
    with pytest.raises(QuoteError):
        quotes.accept(ref, customer_id=seeded.customer_id)

    v2 = quotes.revise(ref, owner_id=seeded.owner_id)
    quotes.approve(v2.ref, owner_id=seeded.owner_id)
    assert quotes.accept(v2.ref, customer_id=seeded.customer_id).order.record_id == order_id


def test_another_customer_cannot_see_or_accept_the_quote(store, seeded, quotes):
    order_id, ref = approved_quote(store, seeded, quotes)
    stranger = uuid.uuid4()

    for attempt in (lambda: quotes.customer_view(order_id, customer_id=stranger),
                    lambda: quotes.accept(ref, customer_id=stranger)):
        with pytest.raises(QuoteError) as error:
            attempt()
        assert error.value.code == "NOT_FOUND"
    assert load(store, Order, order_id).status == OrderStatus.QUOTED
