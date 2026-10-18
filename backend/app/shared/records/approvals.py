"""Owner approval requests bound to one record version (Stage 1 Fig. 8; §5.3).

Saving a draft never creates or implies an approval: ``ApprovalService``
(BAKE-15) creates one request per (kind, subject, version) and records the
owner's decision here. Older decisions stay as history.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.records._common import (
    ApprovalDecision,
    RecordMixin,
    Timestamp,
    new_id,
    utcnow,
)


class ApprovalRequest(RecordMixin, Base):
    __tablename__ = "approval_requests"
    __record_type__ = "ApprovalRequest"
    __record_id__ = "request_id"
    __table_args__ = (
        UniqueConstraint(
            "kind", "subject_id", "subject_version", name="uq_approval_requests_subject_version"
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN "
            f"({', '.join(repr(str(value)) for value in ApprovalDecision)})",
            name="decision",
        ),
        # A decision always records who decided and when; a pending request has neither.
        CheckConstraint(
            "(decision IS NULL) = (decided_by IS NULL) AND (decision IS NULL) = (decided_at IS NULL)",
            name="decision_complete",
        ),
        CheckConstraint("subject_version > 0", name="subject_version_positive"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))
    # Polymorphic RecordRef: existence is checked by the service, not by an FK.
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    subject_version: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str | None] = mapped_column(String(16))
    # Must be the owner account; ApprovalService checks the role.
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_accounts.user_id"))
    decided_at: Mapped[datetime | None] = mapped_column(Timestamp)
    # [R10] Set when a recorded approval can no longer release (UC-04 8a).
    invalidated_at: Mapped[datetime | None] = mapped_column(Timestamp)
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
