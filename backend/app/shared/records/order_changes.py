"""UC-02 confirmed requests and pending commercial amendments [R9]."""
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.shared.records._common import InPlaceVersioned, Json, Timestamp, new_id, utcnow


class OrderChangeRequest(InPlaceVersioned, Base):
    __tablename__ = "order_change_requests"
    __record_type__ = "OrderChangeRequest"
    __record_id__ = "change_id"
    __table_args__ = (
        CheckConstraint("review_state IN ('Pending','AwaitingApproval','Quoted','Applied','Rejected','Withdrawn')", name="review_state"),
        CheckConstraint("kind IN ('Change','Cancel','Refund')", name="kind"),
    )
    change_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"))
    expected_order_version: Mapped[int] = mapped_column(Integer)
    expected_state: Mapped[dict] = mapped_column(Json)
    kind: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    proposed_terms: Mapped[dict] = mapped_column(Json)
    review_state: Mapped[str] = mapped_column(String(24), default="Pending")
    owner_note: Mapped[str | None] = mapped_column(Text)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    quote_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(Timestamp)
