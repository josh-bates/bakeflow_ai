"""Append-only audit entries and the shared constraint guard row (§5.3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.records._common import Json, Timestamp, new_id, utcnow


class AuditEntry(Base):
    """Who changed which record version, when, and from what to what.

    Rows are only ever inserted: ``ValidatedRecordStore`` refuses to update or
    delete them.
    """

    __tablename__ = "audit_entries"
    __table_args__ = (Index("ix_audit_entries_subject", "subject_type", "subject_id"),)

    audit_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    subject_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # NULL for record types that are not versioned.
    subject_version: Mapped[int | None] = mapped_column(Integer)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"))
    action: Mapped[str] = mapped_column(String(64))
    before_data: Mapped[dict | None] = mapped_column(Json)
    after_data: Mapped[dict | None] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    # [R11] Links to workflow_transitions once BAKE-8 adds that table.
    transition_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class ConstraintGuard(Base):
    """The single row every stock- or capacity-changing write locks first [R1]."""

    __tablename__ = "constraint_guard"
    __table_args__ = (CheckConstraint("bakery_id = 1", name="single_bakery"),)

    bakery_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
