"""Shopping lists, production schedules and tasks (Stage 1 Fig. 7; §5.3).

``ProductionSchedule`` is the logical head: ``version`` is the newest planned
content and ``released_version`` the last one that passed the guarded release
[C1]. Each planned version is an immutable ``ProductionScheduleVersion`` row
that owns its tasks. Task progress changes ``ProductionTask.status`` only,
never the schedule version (Stage 1 business invariant 10).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.records._common import (
    InPlaceVersioned,
    Json,
    RecordMixin,
    TaskStatus,
    Timestamp,
    new_id,
    one_of,
    utcnow,
)


class ShoppingList(RecordMixin, Base):
    """UC-03 result: needs and shortages for one order version; no stock change."""

    __tablename__ = "shopping_lists"
    __record_type__ = "ShoppingList"
    __record_id__ = "list_id"

    list_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), index=True)
    source_order_version: Mapped[int] = mapped_column(Integer)
    # {inventory_item_id: "decimal quantity"}; shortages hold positive values only.
    requirements: Mapped[dict] = mapped_column(Json)
    shortages: Mapped[dict] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class ProductionSchedule(InPlaceVersioned, Base):
    __tablename__ = "production_schedules"
    __record_type__ = "ProductionSchedule"
    __record_id__ = "schedule_id"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "released_version IS NULL OR (released_version >= 1 AND released_version <= version)",
            name="released_version_range",
        ),
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    released_version: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class ProductionScheduleVersion(RecordMixin, Base):
    """The planned content of one schedule version; never edited once saved.

    Saved through the store (audited) and append-only: its snapshot is what a
    released plan commits, so changing it would change stock silently [C1].
    """

    __tablename__ = "production_schedule_versions"
    __record_type__ = "ProductionScheduleVersion"
    __record_id__ = "schedule_id"
    __append_only__ = True
    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="date_range"),
        CheckConstraint("daily_capacity_minutes > 0", name="capacity_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_schedules.schedule_id"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    daily_capacity_minutes: Mapped[int] = mapped_column(Integer)
    # [C1] {order_id: {inventory_item_id: "decimal quantity"}} committed on release.
    requirements_snapshot: Mapped[dict] = mapped_column(Json)
    # [R10] The order RecordRefs this version was planned from.
    source_order_refs: Mapped[list] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class ProductionTask(RecordMixin, Base):
    __tablename__ = "production_tasks"
    __record_type__ = "ProductionTask"
    __record_id__ = "task_id"
    __table_args__ = (
        ForeignKeyConstraint(
            ["schedule_id", "schedule_version"],
            ["production_schedule_versions.schedule_id", "production_schedule_versions.version"],
            name="fk_production_tasks_schedule_version",
        ),
        UniqueConstraint(
            "schedule_id", "schedule_version", "logical_task_id", name="uq_production_tasks_logical"
        ),
        one_of("status", TaskStatus, "status"),
        # Half-open interval [start_at, end_at) (IP-05).
        CheckConstraint("start_at < end_at", name="interval"),
        CheckConstraint("lock_version > 0", name="lock_version_positive"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    # [C1] Stable across schedule versions for unchanged work.
    logical_task_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=new_id)
    schedule_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    schedule_version: Mapped[int] = mapped_column(Integer)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), index=True)
    task_kind: Mapped[str] = mapped_column(String(32))
    start_at: Mapped[datetime] = mapped_column(Timestamp)
    end_at: Mapped[datetime] = mapped_column(Timestamp)
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.PLANNED)
    # [R8] optional compare-and-swap token for task edits.
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
