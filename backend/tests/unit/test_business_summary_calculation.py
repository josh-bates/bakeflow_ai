"""Business summary arithmetic: capacity, overlap and sufficiency (BAKE-25)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.marketing import (
    MIN_LEDGER_TRANSACTIONS,
    BusinessSummary,
    CapacityPosition,
    ProductDemand,
)
from app.marketing.summary import _overlap_minutes
from app.pricing_bookkeeping.profitability import ProfitabilitySummary, ProfitLine

HISTORY_START = datetime(2026, 10, 1, tzinfo=UTC)
HISTORY_END = datetime(2026, 11, 1, tzinfo=UTC)
WINDOW_START = datetime(2026, 11, 2, tzinfo=UTC)


class _Task:
    """Stands in for a ProductionTask; only the two times are read."""

    def __init__(self, start_at: datetime, end_at: datetime):
        self.start_at = start_at
        self.end_at = end_at


def capacity(*, start: date, end: date, committed: int = 0) -> CapacityPosition:
    return CapacityPosition(
        daily_capacity_minutes=480,
        start_date=start,
        end_date=end,
        committed_minutes=committed,
    )


# --- capacity counts whole local days, both ends included ------------------


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (date(2026, 11, 2), date(2026, 11, 2), 1),
        (date(2026, 11, 2), date(2026, 11, 8), 7),
        (date(2026, 4, 1), date(2026, 4, 8), 8),
    ],
    ids=["one_day", "a_week", "across_the_end_of_daylight_saving"],
)
def test_days_in_the_planning_window(start, end, expected):
    """Dates, not elapsed time: daylight saving cannot add or lose a day."""
    assert capacity(start=start, end=end).days == expected


def test_free_capacity_is_what_is_left_after_active_plans():
    position = capacity(start=date(2026, 11, 2), end=date(2026, 11, 8), committed=600)

    assert position.total_minutes == 3360
    assert position.free_minutes == 2760


def test_an_over_committed_window_has_no_free_capacity():
    position = capacity(start=date(2026, 11, 2), end=date(2026, 11, 2), committed=900)

    assert position.free_minutes == 0


# --- minutes a task takes out of the window -------------------------------


def test_a_task_inside_the_window_counts_in_full():
    task = _Task(WINDOW_START + timedelta(hours=2), WINDOW_START + timedelta(hours=4))

    assert _overlap_minutes(task, WINDOW_START, WINDOW_START + timedelta(days=1)) == 120


def test_a_task_that_starts_before_the_window_is_clipped():
    task = _Task(WINDOW_START - timedelta(hours=3), WINDOW_START + timedelta(hours=1))

    assert _overlap_minutes(task, WINDOW_START, WINDOW_START + timedelta(days=1)) == 60


def test_a_task_that_runs_past_the_window_is_clipped():
    end = WINDOW_START + timedelta(days=1)
    task = _Task(end - timedelta(minutes=30), end + timedelta(hours=5))

    assert _overlap_minutes(task, WINDOW_START, end) == 30


def test_a_task_outside_the_window_counts_for_nothing():
    task = _Task(WINDOW_START + timedelta(days=9), WINDOW_START + timedelta(days=9, hours=2))

    assert _overlap_minutes(task, WINDOW_START, WINDOW_START + timedelta(days=1)) == 0


# --- UC-07 2a: is there enough recorded history to plan from? -------------


def summary(*, transactions: int, products: int = 1, stock: tuple = ("flour",)) -> BusinessSummary:
    return BusinessSummary(
        history_start=HISTORY_START,
        history_end=HISTORY_END,
        planning_start=date(2026, 11, 2),
        planning_end=date(2026, 11, 8),
        profit=ProfitabilitySummary(
            overall=ProfitLine(income=Decimal("0.00"), transactions=transactions),
            general=ProfitLine(),
            by_order=(),
            by_product=(),
        ),
        top_products_by_units=tuple(
            ProductDemand(uuid.uuid4(), f"product {n}", units_sold=10 - n)
            for n in range(products)
        ),
        stock=stock,
        capacity=capacity(start=date(2026, 11, 2), end=date(2026, 11, 8)),
    )


def test_sales_and_a_full_ledger_are_enough_to_plan_from():
    """by_product is empty here, as it is for real order payments."""
    assert summary(transactions=MIN_LEDGER_TRANSACTIONS).is_sufficient is True


def test_too_few_transactions_is_not_enough():
    assert summary(transactions=MIN_LEDGER_TRANSACTIONS - 1).is_sufficient is False


def test_no_sales_history_is_not_enough():
    assert summary(transactions=50, products=0).is_sufficient is False


def test_no_stock_records_is_not_enough():
    assert summary(transactions=50, stock=()).is_sufficient is False


def test_overall_and_general_are_exposed_beside_the_ranking():
    """The agent's profit claims must use the figure that has the costs in it."""
    built = summary(transactions=10)

    assert built.overall is built.profit.overall
    assert built.general is built.profit.general
