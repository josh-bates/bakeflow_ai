"""IP-01 and IP-04: task progress and the once-only deduction (BAKE-33).

``TaskProgress.record`` runs the architecture's ``TASK_STARTED`` and
``TASK_COMPLETED`` rows for one task. It works inside
``store.constraint_guard()``, so it runs one at a time with the guarded release
that carries progress into a revised version [C1]:

- Progress changes ``ProductionTask.status`` only, never
  ``ProductionSchedule.version`` (Stage 1 business invariant 10).
- Only tasks of the schedule's effective ``released_version`` change. While a
  revised version is not released yet (awaiting approval or needing
  revision), a task it moves or removes is paused; a task it keeps unchanged
  carries on, and the release copies its latest status.
- The order's first task to start (or finish) moves the order from Scheduled
  to InProduction (``TX-PROGRESS``).
- Completing the last of the order's tasks in that version runs
  ``TX-DEDUCT`` in the same transaction: one ``OrderDeduction`` holding what
  the released snapshot committed for the order (IP-02), stock lowered by
  exactly that, and the order Completed. Fig. 10: 24 vanilla cupcakes use
  600 g of the 1,000 g flour, leaving 400 g. If stock no longer covers it, or
  anything fails, the task change rolls back too, so a completion is never
  reported without its deduction.
- Repeating a change already recorded returns it again: a repeated completion
  returns the saved deduction and lowers stock no further. A repeat on a task
  of a replaced version is refused like a new change, unless the order is
  already Completed: then the deduction is final and the repeat can only be a
  retry of what was recorded.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory_production.release import _describe, _planned
from app.inventory_production.scheduling import _amounts
from app.shared import RecordNotFound, RecordRef, ValidatedRecordStore
from app.shared.records import (
    BakerySettings,
    InventoryItem,
    Order,
    OrderDeduction,
    OrderDeductionLine,
    OrderStatus,
    ProductionSchedule,
    ProductionScheduleVersion,
    ProductionTask,
    TaskStatus,
)
from app.shared.store import StoreError
from app.workflow.order_flow import OrderStage, WorkflowService

TASK_ACTIONS = {TaskStatus.IN_PROGRESS: "task.started", TaskStatus.COMPLETED: "task.completed"}
ORDER_ACTIONS = {OrderStage.IN_PRODUCTION: "order.production_started", OrderStage.COMPLETED: "order.completed"}
DEDUCTION_ACTION = "order.deducted"
STOCK_ACTION = "stock.deducted"

# The statuses each recorded change may start from. Nothing here sets Blocked.
_FROM = {
    TaskStatus.IN_PROGRESS: (TaskStatus.PLANNED, TaskStatus.BLOCKED),
    TaskStatus.COMPLETED: (TaskStatus.PLANNED, TaskStatus.BLOCKED, TaskStatus.IN_PROGRESS),
}


class ProgressRefused(StoreError):
    """The task cannot change now; nothing was saved. The message says why."""

    code = "PROGRESS_REFUSED"


@dataclass(frozen=True)
class DeductedLine:
    item_id: uuid.UUID
    ingredient: str
    unit: str
    quantity: Decimal
    # Stock on hand when the outcome was read.
    on_hand: Decimal


@dataclass(frozen=True)
class Deduction:
    deduction_id: uuid.UUID
    schedule_version: int
    deducted_at: datetime
    lines: tuple[DeductedLine, ...]


@dataclass(frozen=True)
class ProgressOutcome:
    task_id: uuid.UUID
    task_status: str
    order_ref: RecordRef
    order_status: str
    # The order's deduction, once its last task is completed.
    deduction: Deduction | None = None
    # The change was already recorded; nothing was saved again.
    replayed: bool = False


class TaskProgress:
    def __init__(self, store: ValidatedRecordStore) -> None:
        self.store = store
        self.workflow = WorkflowService()

    def record(
        self, schedule_id: uuid.UUID, task_id: uuid.UUID, status: TaskStatus, *, actor_id: uuid.UUID
    ) -> ProgressOutcome:
        """Set the task ``InProgress`` or ``Completed``, and deduct if it was the order's last.

        Raises ``RecordNotFound`` for a task not in the schedule and
        ``ProgressRefused`` when the change is not allowed now.
        """
        if status not in _FROM:
            raise ValueError(f"progress sets a task InProgress or Completed, not {status}")
        with self.store.constraint_guard() as session:
            found = session.get(ProductionTask, task_id)
            if found is None or found.schedule_id != schedule_id:
                raise RecordNotFound(f"ProductionTask {task_id}")
            schedule = self.store.load(session, ProductionSchedule, schedule_id)
            order = self.store.load(session, Order, found.order_id, lock=True)
            # The order's tasks in this version, locked in one stable order.
            tasks = session.scalars(
                select(ProductionTask)
                .where(
                    ProductionTask.schedule_id == schedule_id,
                    ProductionTask.schedule_version == found.schedule_version,
                    ProductionTask.order_id == order.order_id,
                )
                .order_by(ProductionTask.task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            task = next(t for t in tasks if t.task_id == task_id)
            if task.status == status:
                if order.status != OrderStatus.COMPLETED:
                    self._check_version(schedule, task)
                return self._outcome(session, task, order, replayed=True)
            self._check(session, schedule, task, order, status)
            if order.status == OrderStatus.SCHEDULED:
                self._move(session, order, OrderStage.IN_PRODUCTION, actor_id)
            task.status = status
            self.store.commit(session, task, actor_id=actor_id, action=TASK_ACTIONS[status])
            if all(t.status == TaskStatus.COMPLETED for t in tasks):
                self._deduct(session, schedule, order, actor_id)
            return self._outcome(session, task, order)

    def _check(
        self,
        session: Session,
        schedule: ProductionSchedule,
        task: ProductionTask,
        order: Order,
        status: TaskStatus,
    ) -> None:
        """Refuse a change that the effective version, a pending revision or the order rules out."""
        from app.sales.changes import pending_change
        if pending_change(session, order.order_id):
            raise ProgressRefused("This order has a pending change or refund review; affected work is paused.")
        if task.status not in _FROM[status]:
            raise ProgressRefused(f"This task is already {task.status.lower()}.")
        self._check_version(schedule, task)
        released = schedule.released_version
        if schedule.version != released and not self._kept(session, task, schedule.version):
            zone = ZoneInfo(session.scalars(select(BakerySettings.time_zone)).one())
            raise ProgressRefused(
                f"{_describe(task, zone)} is paused: version {schedule.version} of this plan moves"
                f" or removes it and is not released yet. Approve version {schedule.version} or"
                " request a revised plan that keeps the task."
            )
        label = f"Order {str(order.order_id)[:8]}"
        if order.status not in (OrderStatus.SCHEDULED, OrderStatus.IN_PRODUCTION):
            raise ProgressRefused(f"{label} is {order.status.lower()}.")
        planned = session.get(ProductionScheduleVersion, (schedule.schedule_id, released))
        source = {ref.record_id: ref.version for ref in map(RecordRef.from_json, planned.source_order_refs)}
        if source.get(order.order_id) != order.version:
            raise ProgressRefused(f"{label} has changed since this plan was released. Request a revised plan.")

    @staticmethod
    def _check_version(schedule: ProductionSchedule, task: ProductionTask) -> None:
        """Refuse a task outside the plan's effective ``released_version``."""
        version, released = task.schedule_version, schedule.released_version
        if released is None or version > released:
            raise ProgressRefused(
                f"Version {version} of this plan is not released yet; its tasks can start once"
                " it is approved and released."
            )
        if version != released:
            raise ProgressRefused(
                f"Version {version} of this plan was replaced by version {released}."
                f" Record progress on version {released}."
            )

    @staticmethod
    def _kept(session: Session, task: ProductionTask, latest: int) -> bool:
        """Whether version ``latest`` keeps ``task`` unchanged, so release will carry its status [C1]."""
        kept = session.scalars(
            select(ProductionTask).where(
                ProductionTask.schedule_id == task.schedule_id,
                ProductionTask.schedule_version == latest,
                ProductionTask.logical_task_id == task.logical_task_id,
            )
        ).one_or_none()
        return kept is not None and _planned(kept) == _planned(task)

    def _deduct(
        self, session: Session, schedule: ProductionSchedule, order: Order, actor_id: uuid.UUID
    ) -> None:
        """TX-DEDUCT: the deduction, the lower stock and the Completed order together."""
        planned = session.get(ProductionScheduleVersion, (schedule.schedule_id, schedule.released_version))
        needs = {
            uuid.UUID(item): Decimal(quantity)
            for item, quantity in planned.requirements_snapshot[str(order.order_id)].items()
            if Decimal(quantity) > 0
        }
        items = [self.store.load(session, InventoryItem, item_id, lock=True) for item_id in sorted(needs)]
        short = {
            item.item_id: needs[item.item_id] - item.quantity_on_hand
            for item in items
            if item.quantity_on_hand < needs[item.item_id]
        }
        if short:
            raise ProgressRefused(
                f"Stock does not cover order {str(order.order_id)[:8]}: short of {_amounts(session, short)}."
                " Nothing was recorded. Record the delivery, then mark the task done again."
            )
        deduction = OrderDeduction(
            order_id=order.order_id,
            schedule_id=schedule.schedule_id,
            schedule_version=schedule.released_version,
            actor_id=actor_id,
            lines=[OrderDeductionLine(item_id=item_id, quantity=needs[item_id]) for item_id in sorted(needs)],
        )
        self.store.commit(session, deduction, actor_id=actor_id, action=DEDUCTION_ACTION)
        for item in items:
            item.quantity_on_hand -= needs[item.item_id]
            self.store.commit(
                session, item, actor_id=actor_id, action=STOCK_ACTION,
                expected_version=item.version, revise=True,
            )
        self._move(session, order, OrderStage.COMPLETED, actor_id)

    def _move(self, session: Session, order: Order, stage: OrderStage, actor_id: uuid.UUID) -> None:
        self.workflow.move_order(
            self.store.ref(order), OrderStage(order.status), stage,
            actor=str(actor_id), event_type="taskProgressed",
        )
        order.status = stage.value
        self.store.commit(
            session, order, actor_id=actor_id, action=ORDER_ACTIONS[stage], expected_version=order.version
        )

    def _outcome(
        self, session: Session, task: ProductionTask, order: Order, *, replayed: bool = False
    ) -> ProgressOutcome:
        row = session.scalars(
            select(OrderDeduction).where(OrderDeduction.order_id == order.order_id)
        ).one_or_none()
        return ProgressOutcome(
            task_id=task.task_id,
            task_status=task.status,
            order_ref=self.store.ref(order),
            order_status=order.status,
            deduction=None if row is None else _deduction(session, row),
            replayed=replayed,
        )


def _deduction(session: Session, row: OrderDeduction) -> Deduction:
    items = {
        item.item_id: item
        for item in session.scalars(
            select(InventoryItem).where(InventoryItem.item_id.in_([line.item_id for line in row.lines]))
        )
    }
    lines = sorted(row.lines, key=lambda line: (items[line.item_id].ingredient_name, line.item_id))
    return Deduction(
        deduction_id=row.deduction_id,
        schedule_version=row.schedule_version,
        deducted_at=row.deducted_at,
        lines=tuple(
            DeductedLine(
                item_id=line.item_id,
                ingredient=items[line.item_id].ingredient_name,
                unit=items[line.item_id].unit,
                quantity=line.quantity,
                on_hand=items[line.item_id].quantity_on_hand,
            )
            for line in lines
        ),
    )
