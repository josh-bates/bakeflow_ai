"""BAKE-20: ProductionService.validate enforces IP-05 on a proposed plan.

Jira tests: the validator rejects overlap, an over-capacity day, a late task
and an empty schedule. Also coverage, plan days and the one-team rule against
tasks of other released plans.
"""

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.inventory_production.production import (
    OrderDemand,
    PlannedTask,
    PlanWindow,
    ProductionService,
    ViolationKind,
)

SYDNEY = ZoneInfo("Australia/Sydney")
DAY = date(2026, 10, 1)
WINDOW = PlanWindow(DAY, DAY, daily_capacity_minutes=240, zone=SYDNEY)
A, B = uuid.UUID(int=1), uuid.UUID(int=2)


def at(hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SYDNEY)


def task(order, kind, start, minutes):
    return PlannedTask(order, kind, start, start + timedelta(minutes=minutes))


def demand(hour=12, work=90):
    return OrderDemand(required_at=at(hour), work_minutes=work)


def figure10_plan(order=A, start=at(6)):
    """24 cupcakes, 90 minutes of work: bake 06:00-07:00, finish 07:00-07:30."""
    return [task(order, "bake", start, 60), task(order, "finish", start + timedelta(minutes=60), 30)]


def kinds(violations):
    return [v.kind for v in violations]


def validate(tasks, orders=None, busy=()):
    return ProductionService().validate(
        tasks, window=WINDOW, orders=orders or {A: demand()}, busy=busy
    )


def test_feasible_plan_has_no_violations():
    assert validate(figure10_plan()) == []


def test_empty_schedule_is_rejected():
    assert kinds(validate([])) == [ViolationKind.EMPTY]


def test_overlapping_tasks_are_rejected():
    plan = [task(A, "bake", at(6), 60), task(A, "finish", at(6, 50), 30)]

    assert ViolationKind.OVERLAP in kinds(validate(plan))


def test_over_capacity_day_is_rejected():
    # Two 150-minute orders on one day: 300 minutes against 240.
    plan = figure10_plan(A, at(6)) + figure10_plan(B, at(8))
    plan += [task(A, "finish", at(10), 60), task(B, "finish", at(11), 60)]
    orders = {A: demand(work=150), B: demand(work=150)}

    violations = validate(plan, orders)

    assert kinds(violations) == [ViolationKind.OVER_CAPACITY]
    assert "300 minutes" in violations[0].message


def test_late_task_is_rejected():
    violations = validate(figure10_plan(start=at(11)))  # finished 12:30, due 12:00

    assert kinds(violations) == [ViolationKind.LATE]
    assert violations[0].order_id == A


def test_every_order_needs_a_bake_and_a_finish_task():
    violations = validate([task(A, "bake", at(6), 90)])

    assert kinds(violations) == [ViolationKind.NOT_COVERED]
    assert "finish" in violations[0].message


def test_an_order_left_out_of_the_plan_is_not_covered():
    violations = validate(figure10_plan(A), {A: demand(), B: demand()})

    assert [(v.kind, v.order_id) for v in violations] == [(ViolationKind.NOT_COVERED, B)]


def test_task_for_an_order_outside_the_plan_is_rejected():
    assert ViolationKind.UNKNOWN_ORDER in kinds(validate(figure10_plan(A) + figure10_plan(B, at(8))))


def test_finishing_before_baking_is_rejected():
    plan = [task(A, "finish", at(6), 30), task(A, "bake", at(6, 30), 60)]

    assert kinds(validate(plan)) == [ViolationKind.SEQUENCE]


def test_too_little_work_time_is_rejected():
    plan = [task(A, "bake", at(6), 30), task(A, "finish", at(6, 30), 30)]

    assert kinds(validate(plan)) == [ViolationKind.UNDER_TIME]


@pytest.mark.parametrize(
    "plan",
    [
        figure10_plan(start=at(6, day=date(2026, 10, 2))),  # the day after the plan
        [task(A, "bake", at(23, 30, day=date(2026, 9, 30)), 60)]  # runs past midnight
        + [task(A, "finish", at(6), 30)],
    ],
    ids=["other-day", "past-midnight"],
)
def test_tasks_must_fall_on_the_plan_days(plan):
    assert ViolationKind.OUTSIDE_PLAN in kinds(validate(plan))


def test_task_must_end_after_it_starts():
    plan = figure10_plan() + [PlannedTask(A, "finish", at(8), at(8))]

    assert ViolationKind.INVALID_INTERVAL in kinds(validate(plan))


def test_other_released_plans_share_the_team_and_its_day():
    other = [task(uuid.UUID(int=9), "bake", at(6, 30), 180)]  # 06:30-09:30 elsewhere

    violations = validate(figure10_plan(), busy=other)

    # Overlaps both the bake and the finish, and 90 + 180 minutes exceed the day.
    assert kinds(violations) == [
        ViolationKind.OVERLAP,
        ViolationKind.OVERLAP,
        ViolationKind.OVER_CAPACITY,
    ]


def test_all_problems_are_reported_together():
    plan = [task(A, "finish", at(11), 60), task(A, "bake", at(11, 30), 60)]

    assert set(kinds(validate(plan))) == {
        ViolationKind.SEQUENCE,
        ViolationKind.OVERLAP,
        ViolationKind.LATE,
    }
