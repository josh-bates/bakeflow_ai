"""Column types, enumerations and mixins shared by every record (BAKE-7).

Precision follows architecture §5.3 [R3]: money ``NUMERIC(12,2)``, raw unit
costs ``NUMERIC(12,4)``, quantities ``NUMERIC(12,3)``, margins
``NUMERIC(5,4)``. All timestamps are timezone-aware UTC.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

Money = Numeric(12, 2)
UnitCost = Numeric(12, 4)
Quantity = Numeric(12, 3)
Margin = Numeric(5, 4)
Json = JSONB
Timestamp = DateTime(timezone=True)


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def one_of(column: str, values: type[StrEnum], name: str) -> CheckConstraint:
    """CHECK constraint limiting ``column`` to the values of ``values``."""
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


class Role(StrEnum):
    OWNER = "Owner"
    CUSTOMER = "Customer"


class OrderStatus(StrEnum):
    # Same values as BAKE-8's OrderStage; architecture §5.3 lists only five.
    DRAFT = "Draft"
    QUOTED = "Quoted"
    CONFIRMED = "Confirmed"
    SCHEDULED = "Scheduled"
    IN_PRODUCTION = "InProduction"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class CustomerDecision(StrEnum):
    PENDING = "Pending"
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"


class TransactionKind(StrEnum):
    INCOME = "Income"
    EXPENSE = "Expense"


class TaskStatus(StrEnum):
    # IP-05 / Stage 1 business invariant 10.
    PLANNED = "Planned"
    IN_PROGRESS = "InProgress"
    BLOCKED = "Blocked"
    COMPLETED = "Completed"


class ApprovalDecision(StrEnum):
    APPROVED = "Approved"
    REJECTED = "Rejected"


class Versioning(StrEnum):
    """How a record type keeps versions; read by ``ValidatedRecordStore``."""

    NONE = "none"
    # One row whose ``version`` the store increments on a guarded revision.
    IN_PLACE = "in_place"
    # Composite key (id, version): each revision is a new row, history kept.
    ROWS = "rows"


class RecordMixin:
    """Identifies a record type for ``RecordRef`` and audit entries.

    Subclasses set ``__record_type__`` (the Stage 1 class name) and
    ``__record_id__`` (the name of the identifier attribute).
    """

    __record_type__: str
    __record_id__: str
    __versioning__: Versioning = Versioning.NONE
    # Row-versioned records only: columns that may change on a saved version.
    __mutable_fields__: frozenset[str] = frozenset()
    # Unversioned records that never change once saved (a schedule version [C1]).
    __append_only__: bool = False

    @property
    def record_id(self) -> uuid.UUID:
        return getattr(self, self.__record_id__)


class InPlaceVersioned(RecordMixin):
    __versioning__ = Versioning.IN_PLACE

    # sort_order keeps version as the last column of the table.
    version: Mapped[int] = mapped_column(Integer, default=1, sort_order=100)


class RowVersioned(RecordMixin):
    __versioning__ = Versioning.ROWS

    version: Mapped[int] = mapped_column(Integer, primary_key=True, default=1, sort_order=100)
