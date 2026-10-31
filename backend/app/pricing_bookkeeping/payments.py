"""Local mock payment for confirmed orders (BAKE-39 [O3]; architecture TX-PAYMENT-SUCCESS).

A customer pays for an order that was confirmed from an accepted, current,
owner-approved quote. There is no bank, card data or real provider:
``MockPaymentGateway`` plays the provider's test mode and reports an outcome
for the stored payment.

- ``start`` creates one payment with a stable ``payment_id``. The amount is
  the accepted quote's total, copied once; the client never supplies it.
  Starting again returns the open payment, and an order that is already paid
  is refused.
- ``complete`` handles the provider's callback. A success records the income
  through ``LedgerService`` and settles the payment in **one** transaction, so
  there is never a paid payment without its income or income without a paid
  payment. A failure settles the payment as Failed and records nothing; the
  customer can then start a new payment.
- A success is recorded only if the order is still payable and its accepted
  quote is still the same version, accepted, current and owner-approved: the
  same checks as ``start``. Otherwise the payment settles as Failed with the
  reason, and no income is recorded.
- A callback for a payment that has already settled returns the stored
  outcome (``replayed``) and records nothing, so a repeated success callback
  never doubles the income. A callback whose amount differs from the stored
  amount is refused and changes nothing.

AI agents play no part in amounts or payment state.

Deviation: architecture §4 routes callbacks through
``WorkflowService.handle(PAYMENT_CALLBACK_*)``, which does not exist yet, so
the payment routes call this service directly.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.pricing_bookkeeping.ledger import LedgerService, TransactionInput
from app.shared.approvals import ApprovalKind, ApprovalService
from app.shared.records import (
    Customer,
    CustomerDecision,
    MockPayment,
    Order,
    OrderStatus,
    PaymentStatus,
    Quote,
    TransactionKind,
)
from app.shared.refs import RecordRef
from app.shared.store import RecordNotFound, ValidatedRecordStore

logger = logging.getLogger(__name__)

START_ACTION = "payment.created"
SETTLE_ACTION = "payment.settled"
# A confirmed order can be paid until it is collected; a cancelled one cannot.
PAYABLE = frozenset(
    {OrderStatus.CONFIRMED, OrderStatus.SCHEDULED, OrderStatus.IN_PRODUCTION, OrderStatus.COMPLETED}
)
OUTCOMES = frozenset({PaymentStatus.SUCCEEDED, PaymentStatus.FAILED})


class PaymentError(Exception):
    """A payment step was refused; nothing was stored.

    Codes follow architecture §6.2: ``NOT_FOUND``, ``CONFLICT``,
    ``APPROVAL_REQUIRED`` and ``VALIDATION_FAILED``.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PaymentView:
    payment_id: uuid.UUID
    order_id: uuid.UUID
    quote: RecordRef
    amount: Decimal
    status: str
    transaction_id: uuid.UUID | None
    created_at: datetime
    processed_at: datetime | None
    # True when a repeated start or callback returned what was already stored.
    replayed: bool = False
    # Why a success callback was settled as Failed instead; not stored.
    reason: str | None = None

    @classmethod
    def of(cls, row: MockPayment, *, replayed: bool = False, reason: str | None = None) -> PaymentView:
        return cls(
            payment_id=row.payment_id,
            order_id=row.order_id,
            quote=RecordRef("Quote", row.accepted_quote_id, row.accepted_quote_version),
            amount=row.amount,
            status=row.status,
            transaction_id=row.transaction_id,
            created_at=row.created_at,
            processed_at=row.processed_at,
            replayed=replayed,
            reason=reason,
        )


@dataclass(frozen=True)
class GatewayCallback:
    """What the provider reports for one payment."""

    payment_id: uuid.UUID
    outcome: str
    amount: Decimal


class MockPaymentGateway:
    """Stands in for a provider's test mode: no network, keys or card data.

    The customer chooses the outcome on the mock checkout, as test card
    numbers do on a real sandbox. The reported amount is the stored one.
    """

    def checkout(self, payment: PaymentView, outcome: str) -> GatewayCallback:
        if outcome not in OUTCOMES:
            raise PaymentError("VALIDATION_FAILED", "outcome must be Succeeded or Failed")
        return GatewayCallback(payment.payment_id, outcome, payment.amount)


class PaymentService:
    def __init__(
        self,
        store: ValidatedRecordStore,
        *,
        ledger: LedgerService | None = None,
        approvals: ApprovalService | None = None,
    ):
        self.store = store
        self.ledger = ledger or LedgerService(store)
        self.approvals = approvals or ApprovalService(store)

    def start(self, order_id: uuid.UUID, *, customer_id: uuid.UUID) -> PaymentView:
        """Create the payment for an order's accepted quote, or return the open one."""
        with self.store.unit_of_work() as session:
            order = self._customer_order(session, order_id, customer_id, lock=True)
            from app.sales.changes import pending_change
            if pending_change(session, order_id):
                raise PaymentError("CONFLICT", "Resolve the pending order request before starting a payment.")
            for payment in self._payments(session, order_id):
                if payment.status == PaymentStatus.SUCCEEDED:
                    raise PaymentError("CONFLICT", "this order is already paid")
                if payment.status == PaymentStatus.CREATED:
                    return PaymentView.of(payment, replayed=True)
            quote = self._payable_quote(session, order)
            payment = MockPayment(
                order_id=order.order_id,
                accepted_quote_id=quote.quote_id,
                accepted_quote_version=quote.version,
                amount=quote.total,
            )
            self.store.commit(
                session, payment, actor_id=_customer_user(session, order), action=START_ACTION
            )
            return PaymentView.of(payment)

    def payment_for_customer(self, payment_id: uuid.UUID, *, customer_id: uuid.UUID) -> PaymentView:
        """One of the customer's payments; another customer's is "not found"."""
        with self.store.unit_of_work() as session:
            payment = self._payment(session, payment_id)
            self._customer_order(session, payment.order_id, customer_id)
            return PaymentView.of(payment)

    def payments(self, order_id: uuid.UUID, *, customer_id: uuid.UUID) -> list[PaymentView]:
        """The customer's payments for one order, oldest first."""
        with self.store.unit_of_work() as session:
            self._customer_order(session, order_id, customer_id)
            return [PaymentView.of(payment) for payment in self._payments(session, order_id)]

    def complete(self, callback: GatewayCallback, *, now: datetime | None = None) -> PaymentView:
        """TX-PAYMENT-SUCCESS: settle the payment once, with its income on success."""
        if callback.outcome not in OUTCOMES:
            raise PaymentError("VALIDATION_FAILED", "outcome must be Succeeded or Failed")
        now = now or datetime.now(UTC)
        with self.store.unit_of_work() as session:
            # The order is locked before the payment, the same order as start().
            order_id = self._payment(session, callback.payment_id).order_id
            order = self.store.load(session, Order, order_id, lock=True)
            payment = self.store.load(session, MockPayment, callback.payment_id, lock=True)
            if callback.amount != payment.amount:
                logger.warning(
                    "Payment %s callback amount %s does not match %s; nothing changed",
                    payment.payment_id, callback.amount, payment.amount,
                )
                raise PaymentError(
                    "VALIDATION_FAILED",
                    f"the callback amount {callback.amount} does not match the payment amount"
                    f" {payment.amount}",
                )
            if payment.status != PaymentStatus.CREATED:
                return PaymentView.of(payment, replayed=True)
            actor_id = _customer_user(session, order)
            outcome, reason = callback.outcome, None
            if outcome == PaymentStatus.SUCCEEDED:
                # The same three conditions as start(), held at the moment money
                # is recorded (review of #34): otherwise the payment settles as
                # Failed, so it is closed and the customer can start again.
                reason = self._unpayable(session, order, payment)
                if reason:
                    logger.warning("Payment %s settled as Failed: %s", payment.payment_id, reason)
                    outcome = PaymentStatus.FAILED
            if outcome == PaymentStatus.SUCCEEDED:
                income = self.ledger.record_in(
                    session,
                    actor_id,
                    TransactionInput(
                        kind=TransactionKind.INCOME,
                        amount=payment.amount,
                        source_type="OrderPayment",
                        source_reference=str(payment.payment_id),
                        occurred_at=now,
                        order_id=payment.order_id,
                    ),
                    now=now,
                )
                payment.transaction_id = income.transaction_id
            payment.status = outcome
            payment.processed_at = now
            self.store.commit(
                session, payment, actor_id=actor_id, action=SETTLE_ACTION,
                expected_version=payment.version, revise=True,
            )
            return PaymentView.of(payment, reason=reason)

    # Helpers ----------------------------------------------------------------------

    def _unpayable(self, session: Session, order: Order, payment: MockPayment) -> str | None:
        """Why ``payment`` can no longer be paid; None when it still can."""
        try:
            quote = self._payable_quote(session, order)
        except PaymentError as exc:
            return exc.message
        if (quote.quote_id, quote.version) != (payment.accepted_quote_id, payment.accepted_quote_version):
            return "the order's accepted quote has changed since this payment started"
        return None

    def _payable_quote(self, session: Session, order: Order) -> Quote:
        """The accepted quote, if it is still current and owner-approved."""
        if order.status not in PAYABLE or order.accepted_quote_id is None:
            raise PaymentError("CONFLICT", f"an order that is {order.status} cannot be paid")
        quote = self.store.load(
            session, Quote, order.accepted_quote_id, version=order.accepted_quote_version
        )
        latest = self.store.load(session, Quote, quote.quote_id)
        ref = RecordRef("Quote", quote.quote_id, quote.version)
        from app.sales.changes import only_closed_amendments_after
        abandoned = only_closed_amendments_after(session, quote)
        if (
            quote.customer_decision != CustomerDecision.ACCEPTED
            or quote.source_order_version != order.version
            or (latest.version != quote.version and not abandoned)
            or not self.approvals.is_approved(session, ref, ApprovalKind.QUOTATION, current_only=not abandoned)
        ):
            raise PaymentError(
                "APPROVAL_REQUIRED", "the order's quote is not accepted, current and approved"
            )
        if quote.total <= 0:
            raise PaymentError("CONFLICT", "there is nothing to pay for this order")
        return quote

    def _customer_order(
        self, session: Session, order_id: uuid.UUID, customer_id: uuid.UUID, *, lock: bool = False
    ) -> Order:
        try:
            order = self.store.load(session, Order, order_id, lock=lock)
        except RecordNotFound:
            raise PaymentError("NOT_FOUND", "no such order") from None
        if order.customer_id != customer_id:
            # Same answer as a missing order, so nothing is disclosed.
            raise PaymentError("NOT_FOUND", "no such order")
        return order

    def _payment(self, session: Session, payment_id: uuid.UUID) -> MockPayment:
        try:
            return self.store.load(session, MockPayment, payment_id)
        except RecordNotFound:
            raise PaymentError("NOT_FOUND", "no such payment") from None

    @staticmethod
    def _payments(session: Session, order_id: uuid.UUID) -> list[MockPayment]:
        return list(
            session.scalars(
                select(MockPayment)
                .where(MockPayment.order_id == order_id)
                .order_by(MockPayment.created_at, MockPayment.payment_id)
            )
        )


def _customer_user(session: Session, order: Order) -> uuid.UUID:
    """The paying customer's account, recorded as the actor in the audit log."""
    return session.get(Customer, order.customer_id).user_id
