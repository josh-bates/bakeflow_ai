"""Deterministic feasibility check for a production plan (BAKE-20, UC-04 step 5).

``ProductionService.validate`` decides whether proposed tasks may become a
draft plan; the agent only proposes (architecture §5.2). The rules are IP-05's:

- the plan has at least one task, each with a positive duration;
- every task starts and ends on one bakery-local day inside the plan's days;
- tasks are only for the candidate orders, and every candidate order has a
  bake and a finish task, no finishing before its first bake ends, and at least
  its labour minutes of work in total;
- the single team never works on two tasks at once, including tasks of other
  released plans (``busy``);
- each day's work, with those other tasks, fits the daily capacity;
- every order is finished by its required (collection) time.

Stock (IP-02) is checked separately with ``InventoryService.check_plan``.
All problems are reported together so the owner sees why a plan was refused
(UC-04 5a).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

TASK_KINDS = ("bake", "finish")


class ViolationKind(StrEnum):
    EMPTY = "EMPTY"
    INVALID_INTERVAL = "INVALID_INTERVAL"
    OUTSIDE_PLAN = "OUTSIDE_PLAN"
    UNKNOWN_ORDER = "UNKNOWN_ORDER"
    NOT_COVERED = "NOT_COVERED"
    SEQUENCE = "SEQUENCE"
    UNDER_TIME = "UNDER_TIME"
    OVERLAP = "OVERLAP"
    OVER_CAPACITY = "OVER_CAPACITY"
    LATE = "LATE"


@dataclass(frozen=True)
class Violation:
    kind: ViolationKind
    message: str
    order_id: uuid.UUID | None = None


@dataclass(frozen=True)
class PlannedTask:
    order_id: uuid.UUID
    task_kind: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class PlanWindow:
    """The days being planned, in the bakery's time zone, and one team's daily minutes."""

    start_date: date
    end_date: date
    daily_capacity_minutes: int
    zone: ZoneInfo


@dataclass(frozen=True)
class OrderDemand:
    """What one candidate order requires of the plan."""

    required_at: datetime
    work_minutes: int


class ProductionService:
    def validate(
        self,
        tasks: Sequence[PlannedTask],
        *,
        window: PlanWindow,
        orders: Mapping[uuid.UUID, OrderDemand],
        busy: Sequence[PlannedTask] = (),
    ) -> list[Violation]:
        """IP-05 problems with ``tasks``; an empty list means the plan is feasible."""
        if not tasks:
            return [Violation(ViolationKind.EMPTY, "The plan has no tasks.")]
        violations: list[Violation] = []
        timed = []
        for task in tasks:
            if task.end_at <= task.start_at:
                violations.append(
                    Violation(
                        ViolationKind.INVALID_INTERVAL,
                        f"{_describe(task, window.zone)} does not end after it starts.",
                        task.order_id,
                    )
                )
                continue
            timed.append(task)
            day = _local_day(task, window.zone)
            if day is None:
                violations.append(
                    Violation(
                        ViolationKind.OUTSIDE_PLAN,
                        f"{_describe(task, window.zone)} runs past midnight.",
                        task.order_id,
                    )
                )
            elif not window.start_date <= day <= window.end_date:
                violations.append(
                    Violation(
                        ViolationKind.OUTSIDE_PLAN,
                        f"{_describe(task, window.zone)} is outside the plan's days.",
                        task.order_id,
                    )
                )
            if task.order_id not in orders:
                violations.append(
                    Violation(
                        ViolationKind.UNKNOWN_ORDER,
                        f"{_describe(task, window.zone)} is for an order that is not in this plan.",
                        task.order_id,
                    )
                )
        violations += self._check_orders(timed, window, orders)
        violations += self._check_team(timed, window, busy)
        return violations

    @staticmethod
    def _check_orders(
        tasks: Sequence[PlannedTask], window: PlanWindow, orders: Mapping[uuid.UUID, OrderDemand]
    ) -> list[Violation]:
        violations: list[Violation] = []
        by_order: dict[uuid.UUID, list[PlannedTask]] = defaultdict(list)
        for task in tasks:
            by_order[task.order_id].append(task)
        for order_id, demand in orders.items():
            own = by_order.get(order_id, [])
            label = f"Order {_short(order_id)}"
            missing = [kind for kind in TASK_KINDS if all(t.task_kind != kind for t in own)]
            if missing:
                violations.append(
                    Violation(
                        ViolationKind.NOT_COVERED,
                        f"{label} has no {' or '.join(missing)} task.",
                        order_id,
                    )
                )
            bakes = [t for t in own if t.task_kind == "bake"]
            if bakes:
                first_bake_end = min(t.end_at for t in bakes)
                if any(t.task_kind == "finish" and t.start_at < first_bake_end for t in own):
                    violations.append(
                        Violation(
                            ViolationKind.SEQUENCE,
                            f"{label} is finished before its first bake ends.",
                            order_id,
                        )
                    )
            planned = sum((_minutes(t) for t in own), 0)
            if own and planned < demand.work_minutes:
                violations.append(
                    Violation(
                        ViolationKind.UNDER_TIME,
                        f"{label} gets {planned} minutes of work but needs {demand.work_minutes}.",
                        order_id,
                    )
                )
            if own:
                finished = max(t.end_at for t in own)
                if finished > demand.required_at:
                    violations.append(
                        Violation(
                            ViolationKind.LATE,
                            f"{label} is finished at {_clock(finished, window.zone)},"
                            f" after its collection time {_clock(demand.required_at, window.zone)}.",
                            order_id,
                        )
                    )
        return violations

    @staticmethod
    def _check_team(
        tasks: Sequence[PlannedTask], window: PlanWindow, busy: Sequence[PlannedTask]
    ) -> list[Violation]:
        """One team: no two tasks at once, and each day's minutes within capacity."""
        violations: list[Violation] = []
        everything = sorted([*tasks, *busy], key=lambda t: (t.start_at, t.end_at))
        latest: PlannedTask | None = None
        for task in everything:
            if latest is not None and task.start_at < latest.end_at:
                violations.append(
                    Violation(
                        ViolationKind.OVERLAP,
                        f"{_describe(latest, window.zone)} and {_describe(task, window.zone)} overlap.",
                        task.order_id,
                    )
                )
            if latest is None or task.end_at > latest.end_at:
                latest = task
        per_day: dict[date, int] = defaultdict(int)
        for task in everything:
            day = _local_day(task, window.zone)
            if day is not None:
                per_day[day] += _minutes(task)
        for day, minutes in sorted(per_day.items()):
            if window.start_date <= day <= window.end_date and minutes > window.daily_capacity_minutes:
                violations.append(
                    Violation(
                        ViolationKind.OVER_CAPACITY,
                        f"{day:%a %d %b} has {minutes} minutes of work;"
                        f" the team has {window.daily_capacity_minutes}.",
                    )
                )
        return violations


def _local_day(task: PlannedTask, zone: ZoneInfo) -> date | None:
    """The bakery-local day of a task, or None if it runs past midnight."""
    start = task.start_at.astimezone(zone).date()
    # Half-open [start, end): a task may end exactly at midnight.
    last_moment = (task.end_at - timedelta(microseconds=1)).astimezone(zone).date()
    return start if last_moment == start else None


def _minutes(task: PlannedTask) -> int:
    return int((task.end_at - task.start_at).total_seconds() // 60)


def _short(order_id: uuid.UUID) -> str:
    return str(order_id)[:8]


def _clock(moment: datetime, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).strftime("%a %H:%M")


def _describe(task: PlannedTask, zone: ZoneInfo) -> str:
    start, end = task.start_at.astimezone(zone), task.end_at.astimezone(zone)
    return f"The {task.task_kind} task for order {_short(task.order_id)} ({start:%a %H:%M}–{end:%H:%M})"
