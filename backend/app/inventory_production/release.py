"""UC-04 steps 7-9: the owner's decision and the guarded release (BAKE-21).

``ScheduleRelease.approve`` runs the architecture's ``SCHEDULE_APPROVED`` row
for exactly one ``ProductionSchedule`` version:

1. ``ApprovalService.decide`` records the owner's decision (``TX-APPROVAL``).
   A version the schedule has moved past is refused with ``STALE_VERSION``, so
   an old approval never releases a revised plan (T-APPROVAL-STALE).
2. FinalCheck (UC-04 8) repeats, against what is committed now, every check
   the draft passed: each order is still at its planned version, eligible and
   in no other released plan, and still needs what the snapshot commits; stock
   covers the plan after the other released plans (IP-02); the tasks fit the
   current daily capacity, the other released plans' tasks and every deadline
   (IP-05); and the approval is current.
3. Progress carries over [C1]. Each task of the previous released version
   gives its latest status, read under the guard at release time rather than
   when the draft was made, to the new task with the same ``logical_task_id``
   and unchanged times. A started, blocked or completed task that the new
   version moves or drops is refused; the owner keeps it in a revised plan.
   An order completed (and deducted, IP-01) meanwhile is not checked for
   stock or labour again: kept, the version must hold exactly its finished
   tasks, unchanged, so it adds no work that could never be done; left out,
   its finished tasks stay recorded in the earlier version, and their time
   still counts against the day's capacity in every later plan.
4. ``TX-SCHEDULE-RELEASE`` sets ``released_version``, so the new snapshot
   replaces the old commitments in one step. Its orders become Scheduled, and
   Scheduled orders the revision left out become Confirmed again, so they can
   be planned anew.

Decision and release share one ``store.constraint_guard()`` transaction. If
FinalCheck fails (UC-04 8a/8b), the Approved decision is kept as history but
invalidated with the reasons, so it can never release; the owner requests a
revised plan, which gets a new request. Any other error rolls back the
decision too, so no approval is ever left looking usable without a release.
Released is reported only after the commit.

``withhold`` (UC-04 7b, Fig. 22 "withhold / remain pending") audits that the
owner is not approving yet. It records no decision, so the version stays
AwaitingApproval and unreleased, and a later approval or revision resumes
the process. ``schedules`` lists the plans with their derived Fig. 22 state
[R7].
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory_production.inventory import InventoryService, StockCheckError
from app.inventory_production.production import OrderDemand, PlannedTask, ProductionService
from app.inventory_production.scheduling import (
    ProductionPlanner,
    _amounts,
    _window,
    _work_minutes,
)
from app.shared import (
    AlreadyDecided,
    ApprovalKind,
    ApprovalNotRequested,
    ApprovalService,
    NotTheOwner,
    RecordNotFound,
    RecordRef,
    StaleVersionError,
    ValidatedRecordStore,
)
from app.shared.records import (
    ApprovalDecision,
    ApprovalRequest,
    AuditEntry,
    BakerySettings,
    Order,
    OrderStatus,
    Product,
    ProductionSchedule,
    ProductionScheduleVersion,
    ProductionTask,
    Role,
    TaskStatus,
    UserAccount,
)
from app.workflow.order_flow import OrderStage, WorkflowService

RELEASE_ACTION = "schedule.released"
PROGRESS_ACTION = "schedule.progress_carried"
ORDER_ACTION = "order.schedule_released"
WITHHOLD_ACTION = "approval.withheld"


class ReleaseStatus(StrEnum):
    # The final check passed and the version is the effective plan.
    RELEASED = "Released"
    # The decision is recorded but invalidated; ``problems`` says why (8a/8b).
    BLOCKED = "ReleaseBlocked"
    # The owner is not approving yet (7b); still pending, nothing released.
    WITHHELD = "Withheld"


@dataclass(frozen=True)
class ReleaseOutcome:
    status: ReleaseStatus
    schedule_ref: RecordRef
    problems: tuple[str, ...] = ()


class ScheduleState(StrEnum):
    """Fig. 22 state of a schedule's latest version, derived, never stored [R7]."""

    # Saved before approval requests existed; request a revised plan.
    DRAFT = "Draft"
    AWAITING_APPROVAL = "AwaitingApproval"
    NEEDS_REVISION = "NeedsRevision"
    # Final checks passed and the guarded save committed: released.
    APPROVED = "Approved"


@dataclass(frozen=True)
class TaskView:
    task_id: uuid.UUID
    order_id: uuid.UUID
    task_kind: str
    start_at: datetime
    end_at: datetime
    status: str


@dataclass(frozen=True)
class VersionView:
    version: int
    start_date: date
    end_date: date
    tasks: tuple[TaskView, ...]


@dataclass(frozen=True)
class ScheduleView:
    schedule_ref: RecordRef
    state: ScheduleState
    latest: VersionView
    # The effective released version, when it is not the latest one.
    released: VersionView | None
    released_version: int | None
    notice: str = ""


@dataclass
class _Checked:
    """What FinalCheck read under the guard, for the save."""

    schedule: ProductionSchedule
    orders: list[Order]
    planned: set[uuid.UUID]
    carried: list[tuple[ProductionTask, str]]


class ScheduleRelease:
    def __init__(self, store: ValidatedRecordStore) -> None:
        self.store = store
        self.approvals = ApprovalService(store)
        self.inventory = InventoryService(store)
        self.production = ProductionService()
        self.workflow = WorkflowService()

    def approve(self, schedule_id: uuid.UUID, version: int, *, owner_id: uuid.UUID) -> ReleaseOutcome:
        """Approve ``version`` and release it if FinalCheck passes."""
        ref = RecordRef("ProductionSchedule", schedule_id, version)
        with self.store.constraint_guard() as session:
            request_id = self._request_id(session, ref)
            self.approvals.decide(session, request_id, ApprovalDecision.APPROVED, owner_id=owner_id)
            problems, checked = self._final_check(session, ref)
            if problems:
                self.approvals.invalidate(
                    session, ref, ApprovalKind.SCHEDULE, " ".join(problems), actor_id=owner_id
                )
                outcome = ReleaseOutcome(ReleaseStatus.BLOCKED, ref, tuple(problems))
            else:
                self._save(session, ref, checked, owner_id=owner_id)
                outcome = ReleaseOutcome(ReleaseStatus.RELEASED, ref)
        return outcome

    def withhold(self, schedule_id: uuid.UUID, version: int, *, owner_id: uuid.UUID) -> ReleaseOutcome:
        """Audit that the owner is not approving ``version`` yet (UC-04 7b).

        No decision is recorded, so the request stays pending: the version
        remains AwaitingApproval and unreleased until the owner approves it
        or requests a revised plan. A decided or superseded version is
        refused, as ``ApprovalService.decide`` would refuse it.
        """
        ref = RecordRef("ProductionSchedule", schedule_id, version)
        with self.store.unit_of_work() as session:
            request = session.get(
                ApprovalRequest, self._request_id(session, ref), with_for_update=True, populate_existing=True
            )
            if request.decision is not None:
                raise AlreadyDecided(f"schedule {schedule_id} version {version} is already {request.decision}")
            account = session.get(UserAccount, owner_id)
            if account is None or account.role != Role.OWNER:
                raise NotTheOwner(f"{owner_id} is not the owner account")
            latest = self.store.load(session, ProductionSchedule, schedule_id).version
            if latest != version:
                raise StaleVersionError(f"schedule {schedule_id} is at version {latest}, not {version}")
            # The request does not change: this audit entry is the ScheduleApprovalWithheld record.
            self.store.commit(session, request, actor_id=owner_id, action=WITHHOLD_ACTION)
        return ReleaseOutcome(ReleaseStatus.WITHHELD, ref)

    def schedules(self) -> tuple[str, list[ScheduleView]]:
        """The bakery's time zone and every schedule, newest first.

        Read from one snapshot: a release that commits while the list is read
        shows either before or after, never an Approved decision on a version
        that looks unreleased.
        """
        with self.store.snapshot() as session:
            zone = session.scalars(select(BakerySettings.time_zone)).one()
            heads = session.scalars(
                select(ProductionSchedule).order_by(
                    ProductionSchedule.created_at.desc(), ProductionSchedule.schedule_id
                )
            ).all()
            return zone, [self._view(session, schedule) for schedule in heads]

    def _final_check(self, session: Session, ref: RecordRef) -> tuple[list[str], _Checked | None]:
        schedule = self.store.load(session, ProductionSchedule, ref.record_id, lock=True)
        planned = session.get(ProductionScheduleVersion, (ref.record_id, ref.version))
        tasks = _tasks(session, ref.record_id, ref.version)
        replaced = ProductionPlanner._released_here(session, ref.record_id)
        elsewhere = ProductionPlanner._released_elsewhere(session, ref.record_id)
        source = {RecordRef.from_json(r).record_id: RecordRef.from_json(r) for r in planned.source_order_refs}

        problems: list[str] = []
        orders: list[Order] = []
        needs: dict[uuid.UUID, dict[uuid.UUID, Decimal]] = {}
        demands: dict[uuid.UUID, OrderDemand] = {}
        products = {p.product_id: p for p in session.scalars(select(Product))}
        # Completed orders of the released version: finished and deducted (IP-01).
        finished: set[uuid.UUID] = set()
        # Every order the release may change, locked in one stable order.
        for order_id in sorted(set(source) | replaced):
            label = f"Order {str(order_id)[:8]}"
            order = self.store.load(session, Order, order_id, lock=True)
            orders.append(order)
            if order_id in replaced and order.status == OrderStatus.COMPLETED:
                finished.add(order_id)
            if order_id not in source:
                continue  # left out by this version; released, it is replaced
            if order.version != source[order_id].version:
                problems.append(f"{label} has changed since this plan was made.")
            elif order_id in elsewhere:
                problems.append(f"{label} is now in another released plan.")
            elif order_id in finished:
                # Completed while this version waited [C1]: its tasks must stay
                # as they were and take their Completed status (checked after
                # the carry below), but its stock is already deducted and its
                # work done, so neither is checked again.
                demands[order_id] = OrderDemand(order.required_at, 0)
            elif not (
                order.status == OrderStatus.CONFIRMED
                or (order_id in replaced and order.status in (OrderStatus.SCHEDULED, OrderStatus.IN_PRODUCTION))
            ):
                problems.append(f"{label} is now {order.status}.")
            else:
                try:
                    order_needs = self.inventory.calculate_needs(session, order)
                except StockCheckError as exc:
                    problems.append(f"{label} can no longer be checked: {exc}.")
                    continue
                snapshot = {str(item): str(quantity) for item, quantity in order_needs.items()}
                if snapshot != planned.requirements_snapshot.get(str(order_id)):
                    problems.append(f"{label} needs different ingredients now.")
                needs[order_id] = order_needs
                demands[order_id] = OrderDemand(order.required_at, _work_minutes(order, products))
        if problems:
            return problems, None

        shortages = self.inventory.check_plan(session, needs, exclude_orders=replaced)
        if shortages:
            problems.append(f"Stock no longer covers the plan: short of {_amounts(session, shortages)}.")
        window = _window(session, planned.start_date, planned.end_date)
        violations = self.production.validate(
            [_planned(t) for t in tasks],
            window=window,
            orders=demands,
            busy=ProductionPlanner._busy(
                session, window, ref.record_id, kept={t.logical_task_id for t in tasks}
            ),
        )
        problems += [v.message for v in violations]
        carried, unsafe = self._carry_progress(
            session, schedule, tasks, window.zone, done=finished - set(source)
        )
        problems += unsafe
        problems += _added_to_completed(tasks, carried, finished & set(source), window.zone)
        if not self.approvals.is_approved(session, ref, ApprovalKind.SCHEDULE):
            problems.append("The approval is no longer current.")
        return problems, _Checked(schedule, orders, set(source), carried)

    def _carry_progress(
        self,
        session: Session,
        schedule: ProductionSchedule,
        tasks: Sequence[ProductionTask],
        zone: ZoneInfo,
        *,
        done: set[uuid.UUID],
    ) -> tuple[list[tuple[ProductionTask, str]], list[str]]:
        """[C1] Latest statuses to copy, and started work this version would move.

        ``done`` are completed orders this version leaves out. Their finished
        tasks stay recorded in the earlier version; leaving them out moves no
        work, so it is not refused.
        """
        if schedule.released_version is None:
            return [], []
        previous = session.scalars(
            select(ProductionTask)
            .where(
                ProductionTask.schedule_id == schedule.schedule_id,
                ProductionTask.schedule_version == schedule.released_version,
            )
            .order_by(ProductionTask.task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        by_logical = {t.logical_task_id: t for t in tasks}
        carried: list[tuple[ProductionTask, str]] = []
        unsafe: list[str] = []
        for old in previous:
            new = by_logical.get(old.logical_task_id)
            unchanged = new is not None and _planned(new) == _planned(old)
            if unchanged and old.status != TaskStatus.PLANNED:
                carried.append((new, old.status))
            elif not unchanged and old.status != TaskStatus.PLANNED and old.order_id not in done:
                unsafe.append(
                    f"{_describe(old, zone)} is {_status_words(old.status)}, and this plan moves"
                    " or removes it. Keep it unchanged in a revised plan."
                )
        return carried, unsafe

    def _save(self, session: Session, ref: RecordRef, checked: _Checked, *, owner_id: uuid.UUID) -> None:
        """TX-SCHEDULE-RELEASE: copied progress, the effective version and order states."""
        for task, status in checked.carried:
            task.status = status
            self.store.commit(session, task, actor_id=owner_id, action=PROGRESS_ACTION)
        schedule = checked.schedule
        schedule.released_version = ref.version
        self.store.commit(
            session, schedule, actor_id=owner_id, action=RELEASE_ACTION, expected_version=ref.version
        )
        for order in checked.orders:
            if order.order_id in checked.planned and order.status == OrderStatus.CONFIRMED:
                self._move(session, order, OrderStage.SCHEDULED, owner_id)
            elif order.order_id not in checked.planned and order.status == OrderStatus.SCHEDULED:
                self._move(session, order, OrderStage.CONFIRMED, owner_id)

    def _move(self, session: Session, order: Order, stage: OrderStage, owner_id: uuid.UUID) -> None:
        self.workflow.move_order(
            self.store.ref(order), OrderStage(order.status), stage,
            actor=str(owner_id), event_type="scheduleReleased",
        )
        order.status = stage.value
        self.store.commit(
            session, order, actor_id=owner_id, action=ORDER_ACTION, expected_version=order.version
        )

    @staticmethod
    def _request_id(session: Session, ref: RecordRef) -> uuid.UUID:
        request_id = session.scalars(
            select(ApprovalRequest.request_id).where(
                ApprovalRequest.kind == ApprovalKind.SCHEDULE,
                ApprovalRequest.subject_type == ref.record_type,
                ApprovalRequest.subject_id == ref.record_id,
                ApprovalRequest.subject_version == ref.version,
            )
        ).one_or_none()
        if request_id is None and session.get(ProductionSchedule, ref.record_id) is None:
            raise RecordNotFound(f"ProductionSchedule {ref.record_id}")
        if request_id is None:
            raise ApprovalNotRequested(
                f"no approval was requested for schedule {ref.record_id} version {ref.version}"
            )
        return request_id

    def _view(self, session: Session, schedule: ProductionSchedule) -> ScheduleView:
        ref = self.store.ref(schedule)
        latest = _version_view(session, schedule.schedule_id, schedule.version)
        released = None
        if schedule.released_version not in (None, schedule.version):
            released = _version_view(session, schedule.schedule_id, schedule.released_version)
        request = session.scalars(
            select(ApprovalRequest).where(
                ApprovalRequest.kind == ApprovalKind.SCHEDULE,
                ApprovalRequest.subject_id == schedule.schedule_id,
                ApprovalRequest.subject_version == schedule.version,
            )
        ).one_or_none()
        label = f"Version {schedule.version}"
        if schedule.released_version == schedule.version:
            state, notice = ScheduleState.APPROVED, ""
        elif request is None:
            state, notice = ScheduleState.DRAFT, f"{label} has no approval request. Request a revised plan."
        elif request.decision is None:
            state, notice = ScheduleState.AWAITING_APPROVAL, ""
            if _withheld(session, request):
                notice = (
                    f"You haven't approved {label.lower()} yet. It stays unreleased"
                    " until you approve it or request a revised plan."
                )
        else:
            state = ScheduleState.NEEDS_REVISION
            reason = request.invalidation_reason or "The release did not complete."
            notice = f"{label} could not be released: {reason} Request a revised plan."
        return ScheduleView(ref, state, latest, released, schedule.released_version, notice)


def _added_to_completed(
    tasks: Sequence[ProductionTask],
    carried: Sequence[tuple[ProductionTask, str]],
    completed: set[uuid.UUID],
    zone: ZoneInfo,
) -> list[str]:
    """Tasks a version gives a completed order besides its finished ones.

    The order is deducted and Completed (IP-01), so such a task could never
    be started or done; it would only hold the team's time.
    """
    finished = {task.task_id for task, status in carried if status == TaskStatus.COMPLETED}
    return [
        f"{_describe(t, zone)} adds work to order {str(t.order_id)[:8]}, which is already completed."
        " Request a revised plan."
        for t in tasks
        if t.order_id in completed and t.task_id not in finished
    ]


def _withheld(session: Session, request: ApprovalRequest) -> bool:
    return session.scalars(
        select(AuditEntry.audit_id)
        .where(
            AuditEntry.subject_type == ApprovalRequest.__record_type__,
            AuditEntry.subject_id == request.request_id,
            AuditEntry.action == WITHHOLD_ACTION,
        )
        .limit(1)
    ).first() is not None


def _tasks(session: Session, schedule_id: uuid.UUID, version: int) -> list[ProductionTask]:
    return list(
        session.scalars(
            select(ProductionTask)
            .where(ProductionTask.schedule_id == schedule_id, ProductionTask.schedule_version == version)
            .order_by(ProductionTask.start_at, ProductionTask.task_id)
        )
    )


def _version_view(session: Session, schedule_id: uuid.UUID, version: int) -> VersionView:
    planned = session.get(ProductionScheduleVersion, (schedule_id, version))
    return VersionView(
        version=version,
        start_date=planned.start_date,
        end_date=planned.end_date,
        tasks=tuple(
            TaskView(t.task_id, t.order_id, t.task_kind, t.start_at, t.end_at, t.status)
            for t in _tasks(session, schedule_id, version)
        ),
    )


def _planned(task: ProductionTask) -> PlannedTask:
    return PlannedTask(task.order_id, task.task_kind, task.start_at, task.end_at)


def _describe(task: ProductionTask, zone: ZoneInfo) -> str:
    start, end = task.start_at.astimezone(zone), task.end_at.astimezone(zone)
    return f"The {task.task_kind} task for order {str(task.order_id)[:8]} ({start:%a %H:%M}–{end:%H:%M})"


def _status_words(status: str) -> str:
    return {TaskStatus.IN_PROGRESS: "in progress"}.get(status, status.lower())
