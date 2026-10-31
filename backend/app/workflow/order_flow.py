"""Order workflow stages and allowed moves for the main order flow (BAKE-8).

The order lifecycle is not modelled as a state machine in the Stage 1 report,
where ``Order.status`` is a free String. These stage names are derived from
the UC-01, UC-05 and UC-04 flows and recorded here as the values BAKE-8 uses.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Callable
import time
from typing import Any

from enum import Enum

from pydantic import ValidationError

from app.llm.gateway import LLMCallFailed
from app.shared.records import AIRequestStatus
from app.shared.refs import RecordRef
from app.workflow.ai_requests import Recording


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
    # [BAKE-41] A scheduled order can still be cancelled before production.
    # [BAKE-21] A released revision that leaves it out makes it Confirmed again.
    OrderStage.SCHEDULED: frozenset(
        {OrderStage.IN_PRODUCTION, OrderStage.CANCELLED, OrderStage.CONFIRMED}
    ),
    OrderStage.IN_PRODUCTION: frozenset({OrderStage.COMPLETED}),
    OrderStage.COMPLETED: frozenset(),
    OrderStage.CANCELLED: frozenset(),
}

# One business AI operation gets the initial attempt plus
# WorkflowService.maxRetries = 2 from the Stage 1 class model.
MAX_ATTEMPTS = 3

# [BAKE-41] What counts as "the AI gave us nothing usable": a failed or
# structurally invalid provider reply, or one that does not fit the expected
# schema. Anything else is a defect in our own code and is never retried.
RETRY_ON: tuple[type[Exception], ...] = (LLMCallFailed, ValidationError)


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

    @staticmethod
    def record_saved_order(session, order, before, *, event_id, actor_id, event_kind):
        """Capture a persisted stage in the order's transaction [R11 / O2].

        Called only by the validated store after the order was flushed. An
        in-memory move or AI response never creates a customer notification.
        Business services still own the guards for their transitions.
        """
        from sqlalchemy import select
        from app.shared.records import Customer, WorkflowTransition

        previous = before.get("status") if before else None
        resulting = OrderStage(order.status).value
        if previous == resulting or (before is None and resulting != "Draft"):
            return None
        recipient = session.scalar(select(Customer.user_id).where(
            Customer.customer_id == order.customer_id))
        return WorkflowTransition(
            event_id=event_id, event_kind=event_kind,
            order_id=order.order_id, customer_id=order.customer_id,
            recipient_id=recipient, order_version=order.version,
            from_state=previous, to_state=resulting, actor_id=actor_id,
        )

    def run_ai_task(
        self,
        subject: RecordRef,
        stage: OrderStage,
        attempt_task: Callable[[], Any],
        is_usable: Callable[[Any], bool],
        *,
        actor: str,
        event_type: str,
        retry_on: tuple[type[Exception], ...] = RETRY_ON,
        prompt_version: str | None = None,
    ) -> Any:
        """Run one AI operation, retrying until usable or the budget runs out.

        The budget is owned here, not in the gateway or the agent, so a single
        business operation never exceeds ``MAX_ATTEMPTS`` calls in total.
        Both malformed output and output that fails business validation count.

        [BAKE-41] Only the exceptions in ``retry_on`` are retried. An unusable
        AI reply deserves another attempt; a defect in our own code does not,
        and must never reach the owner reported as an AI failure.

        [BAKE-37] Inside a request, the operation and each attempt are saved
        in ``ai_requests`` [R5] (see ``app.workflow.ai_requests``).
        """
        recording = Recording(subject, event_type, actor=actor, prompt_version=prompt_version)
        try:
            for _ in range(MAX_ATTEMPTS):
                began = time.perf_counter()
                try:
                    result = attempt_task()
                except retry_on as exc:
                    recording.attempt(began, error=exc)
                    continue
                usable = is_usable(result)
                recording.attempt(began, usable=usable)
                if usable:
                    recording.finish(AIRequestStatus.SUCCEEDED)
                    return result
        except BaseException as exc:
            recording.finish(AIRequestStatus.INTERRUPTED, error=exc)
            raise

        self._record(subject, actor, f"{event_type}Failed", stage, stage)
        recording.finish(AIRequestStatus.FAILED)
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