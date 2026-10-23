"""OrderService status changes for the quotation flow (UC-05; added by BAKE-23).

Only the two moves UC-05 needs: Draft -> Quoted when the owner approves a
quote, and Quoted -> Confirmed when the customer accepts it (step 9). Both
follow BAKE-8's ``ALLOWED_MOVES`` and are committed with ``expected_version``
but **without** ``revise=True``: a status change is not a change to what was
ordered, so ``Order.version`` stays the same and the approved quote's
``source_order_version`` and the shopping list still match it (review of #12).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.shared.records import Order, OrderStatus
from app.shared.refs import RecordRef
from app.shared.store import ValidatedRecordStore
from app.workflow.order_flow import OrderStage, TransitionRejected, is_allowed


class OrderService:
    def __init__(self, store: ValidatedRecordStore):
        self.store = store

    def mark_quoted(self, session: Session, order: Order, *, actor_id: uuid.UUID) -> RecordRef:
        """Draft -> Quoted once an owner-approved quote is presented."""
        return self._move(session, order, OrderStatus.QUOTED, actor_id=actor_id, action="order.quoted")

    def confirm(
        self, session: Session, order: Order, quote: RecordRef, *, actor_id: uuid.UUID
    ) -> RecordRef:
        """Quoted -> Confirmed, bound to the accepted quote version (UC-05 step 9)."""
        order.accepted_quote_id = quote.record_id
        order.accepted_quote_version = quote.version
        return self._move(
            session, order, OrderStatus.CONFIRMED, actor_id=actor_id, action="order.confirmed"
        )

    def _move(
        self, session: Session, order: Order, status: OrderStatus, *, actor_id: uuid.UUID, action: str
    ) -> RecordRef:
        current, requested = OrderStage(order.status), OrderStage(status)
        if not is_allowed(current, requested):
            raise TransitionRejected(current, requested)
        order.status = status
        return self.store.commit(
            session, order, actor_id=actor_id, action=action, expected_version=order.version
        )
