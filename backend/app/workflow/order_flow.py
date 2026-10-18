"""Order workflow stages and allowed moves for the main order flow (BAKE-8).

The order lifecycle is not modelled as a state machine in the Stage 1 report,
where ``Order.status`` is a free String. These stage names are derived from
the UC-01, UC-05 and UC-04 flows and recorded here as the values BAKE-8 uses.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

from enum import Enum


class OrderStage(str, Enum):
    """The stages an order moves through, in order."""

    DRAFT = "Draft"
    QUOTED = "Quoted"
    CONFIRMED = "Confirmed"
    SCHEDULED = "Scheduled"
    IN_PRODUCTION = "InProduction"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


ALLOWED_MOVES: dict[OrderStage, frozenset[OrderStage]] = {
    OrderStage.DRAFT: frozenset({OrderStage.QUOTED, OrderStage.CANCELLED}),
    OrderStage.QUOTED: frozenset({OrderStage.CONFIRMED, OrderStage.CANCELLED}),
    OrderStage.CONFIRMED: frozenset({OrderStage.SCHEDULED, OrderStage.CANCELLED}),
    OrderStage.SCHEDULED: frozenset({OrderStage.IN_PRODUCTION}),
    OrderStage.IN_PRODUCTION: frozenset({OrderStage.COMPLETED}),
    OrderStage.COMPLETED: frozenset(),
    OrderStage.CANCELLED: frozenset(),
}


class TransitionRejected(Exception):
    """Raised when a move is not on the allowed list. Nothing is changed."""

    def __init__(self, current: OrderStage, requested: OrderStage):
        self.current = current
        self.requested = requested
        super().__init__(
            f"Cannot move an order from {current.value} to {requested.value}"
        )


def is_allowed(current: OrderStage, requested: OrderStage) -> bool:
    """Return True if moving from ``current`` to ``requested`` is on the list."""
    return requested in ALLOWED_MOVES.get(current, frozenset())


@dataclass(frozen=True)
class RecordRef:
    """Points at one version of one business record (Stage 1 supporting types)."""

    record_type: str
    record_id: str
    version: int


@dataclass(frozen=True)
class AuditEntry:
    """One line in the notebook, matching AuditEntry in the class model."""

    timestamp: datetime
    subject: RecordRef
    actor: str
    event_type: str
    previous_state: str
    resulting_state: str


class WorkflowService:
    """Deterministic control for the main order flow (BAKE-8)."""

    def __init__(self) -> None:
        self._audit_log: list[AuditEntry] = []

    @property
    def audit_log(self) -> tuple[AuditEntry, ...]:
        """Every move recorded so far, oldest first."""
        return tuple(self._audit_log)

    def move_order(
        self,
        subject: RecordRef,
        current: OrderStage,
        requested: OrderStage,
        *,
        actor: str,
        event_type: str,
    ) -> OrderStage:
        """Move an order one stage, or reject it without changing anything."""
        if not is_allowed(current, requested):
            raise TransitionRejected(current, requested)

        self._audit_log.append(
            AuditEntry(
                timestamp=datetime.now(timezone.utc),
                subject=subject,
                actor=actor,
                event_type=event_type,
                previous_state=current.value,
                resulting_state=requested.value,
            )
        )
        return requested