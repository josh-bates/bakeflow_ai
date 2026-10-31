"""BAKE-38: template-first delivery, independent of model/provider availability.

The durable transition is the notification's identity and recovery source.
Only the rendered copy is best-effort; GET never calls AI or performs writes.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.session import get_sessionmaker
from app.sales.routes import current_customer
from app.sales.intake import CustomerIdentity
from app.sales.tracking import EXPLANATIONS
from app.shared.records import Notification, Order, WorkflowTransition
from app.shared.store import StoreError, ValidatedRecordStore


def template(transition: WorkflowTransition) -> str:
    # These are historical updates, not a claim about the order's current stage.
    return f"Order {transition.order_id}: {EXPLANATIONS[transition.to_state][0]}"


def deliver_notification(store: ValidatedRecordStore, transition_id: uuid.UUID) -> None:
    """May be replayed/concurrent. One row per recorded transition/recipient."""
    with store.unit_of_work() as session:
        transition = session.get(WorkflowTransition, transition_id)
        if transition is None:
            return
        # Infrastructure insertion, like auth sessions; no business mutation.
        # PostgreSQL resolves concurrent deliveries without a failed transaction.
        session.connection().execute(insert(Notification).values(
            notification_id=transition.transition_id,
            transition_id=transition.transition_id,
            recipient_id=transition.recipient_id,
            rendered_text=template(transition), render_mode="template",
        ).on_conflict_do_nothing(index_elements=["transition_id", "recipient_id"]))


def list_notifications(store: ValidatedRecordStore, customer: CustomerIdentity) -> list[dict]:
    with store.unit_of_work() as session:
        rows = session.execute(
            select(WorkflowTransition, Notification)
            .join(Order, Order.order_id == WorkflowTransition.order_id)
            .outerjoin(Notification, and_(
                Notification.transition_id == WorkflowTransition.transition_id,
                Notification.recipient_id == WorkflowTransition.recipient_id))
            .where(WorkflowTransition.customer_id == customer.customer_id,
                   WorkflowTransition.recipient_id == customer.user_id,
                   Order.customer_id == customer.customer_id)
            .order_by(WorkflowTransition.created_at.desc(), WorkflowTransition.transition_id.desc())
        ).all()
        return [{"id": str(t.transition_id), "orderId": str(t.order_id),
                 "status": t.to_state, "createdAt": t.created_at.isoformat(),
                 "message": n.rendered_text if n else template(t)} for t, n in rows]


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/api/notifications")
    def notifications(customer: CustomerIdentity = Depends(current_customer)):
        if not settings.database_url:
            raise HTTPException(503, "Notifications are temporarily unavailable.")
        try:
            store = ValidatedRecordStore(get_sessionmaker(settings.database_url))
            return {"notifications": list_notifications(store, customer)}
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "Notifications are temporarily unavailable. Please try again.") from None

    return router
