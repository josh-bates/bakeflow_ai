"""Durable workflow events and their optional rendered delivery (BAKE-38)."""
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.records._common import Timestamp, new_id, utcnow


class WorkflowTransition(Base):
    __tablename__ = "workflow_transitions"
    transition_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    # The order audit identifies this logical event, not the content version:
    # Scheduled -> Confirmed -> Scheduled can happen at the same version.
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    event_kind: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.customer_id"), index=True)
    recipient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"))
    order_version: Mapped[int] = mapped_column(Integer)
    from_state: Mapped[str | None] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("transition_id", "recipient_id"),)
    notification_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    transition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_transitions.transition_id"))
    recipient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"))
    rendered_text: Mapped[str] = mapped_column(Text)
    render_mode: Mapped[str] = mapped_column(String(16), default="template")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
