"""Pending owner exceptions, separate from sale/order lifecycle (BAKE-17)."""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.shared.records._common import InPlaceVersioned, Json, Timestamp, new_id, utcnow


class OwnerReferral(InPlaceVersioned, Base):
    __tablename__ = "owner_referrals"
    __record_type__ = "OwnerReferral"
    __record_id__ = "referral_id"
    __table_args__ = (
        UniqueConstraint("customer_id", "request_key", name="uq_referral_customer_key"),
        CheckConstraint("kind IN ('Intake', 'Refund', 'InProductionChange')", name="kind"),
        CheckConstraint("status IN ('Pending', 'NeedsClarification', 'AwaitingCustomer', 'Completed', 'Closed')", name="status"),
        CheckConstraint("version > 0", name="version_positive"),
    )
    referral_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.customer_id"), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="Pending")
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.order_id"))
    source_order_version: Mapped[int | None] = mapped_column(Integer)
    source_order_status: Mapped[str | None] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    summary: Mapped[dict | None] = mapped_column(Json, nullable=True)
    conversation: Mapped[list] = mapped_column(Json, default=list)
    resolution: Mapped[dict | None] = mapped_column(Json, nullable=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
