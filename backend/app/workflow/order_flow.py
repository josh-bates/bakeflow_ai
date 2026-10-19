"""Order workflow stages and allowed moves for the main order flow (BAKE-8).

The order lifecycle is not modelled as a state machine in the Stage 1 report,
where ``Order.status`` is a free String. These stage names are derived from
the UC-01, UC-05 and UC-04 flows and recorded here as the values BAKE-8 uses.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

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

# One business AI operation gets the initial attempt plus
# WorkflowService.maxRetries = 2 from the Stage 1 class model.
MAX_ATTEMPTS = 3


class AIOperationFailed(Exception):
    """Raised when an AI operation gave no usable result within the budget."""

    def __init__(self, attempts: int):
        self.attempts = attempts
        super().__init__(f"No usable AI result after {attempts} attempts")


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

        self._record(subject, actor, event_type, current, requested)
        return requested

    def run_ai_task(
        self,
        subject: RecordRef,
        stage: OrderStage,
        attempt_task: Callable[[], Any],
        is_usable: Callable[[Any], bool],
        *,
        actor: str,
        event_type: str,
    ) -> Any:
        """Run one AI operation, retrying until usable or the budget runs out.

        The budget is owned here, not in the gateway or the agent, so a single
        business operation never exceeds ``MAX_ATTEMPTS`` calls in total.
        Both malformed output and output that fails business validation count.
        """
        for _ in range(MAX_ATTEMPTS):
            try:
                result = attempt_task()
            except Exception:
                continue
            if is_usable(result):
                return result

        self._record(subject, actor, f"{event_type}Failed", stage, stage)
        raise AIOperationFailed(MAX_ATTEMPTS)

    def _record(
        self,
        subject: RecordRef,
        actor: str,
        event_type: str,
        previous: OrderStage,
        resulting: OrderStage,
    ) -> None:
        self._audit_log.append(
            AuditEntry(
                timestamp=datetime.now(timezone.utc),
                subject=subject,
                actor=actor,
                event_type=event_type,
                previous_state=previous.value,
                resulting_state=resulting.value,
            )
        )