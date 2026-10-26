"""Business summary arithmetic: capacity, period length and sufficiency (BAKE-25)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.marketing import MIN_LEDGER_TRANSACTIONS, BusinessSummary, CapacityPosition
from app.marketing.summary import _days, _overlap_minutes
from app.pricing_bookkeeping.profitability import (
    ProductProfit,
    ProfitabilitySummary,
    ProfitLine,
)

START = datetime(2026, 11, 1, tzinfo=UTC)


class _Task:
    """Stands in for a ProductionTask; only the two times are read."""

    def __init__(self, start_at: datetime, end_at: datetime):
        self.start_at = start_at
        self.end_at = end_at


# --- capacity --------------------------------------------------------------


def test_free_capacity_is_what_is_left_after_released_plans():
    capacity = CapacityPosition(daily_capacity_minutes=480, days=7, committed_minutes=600)

    assert capacity.total_minutes == 3360
    assert capacity.free_minutes == 2760


def test_an_over_committed_period_has_no_free_capacity():
    """Never negative: a promotion cannot borrow time that does not exist."""
    capacity = CapacityPosition(daily_capacity_minutes=480, days=1, committed_minutes=900)

    assert capacity.free_minutes == 0


# --- the length of the period ---------------------------------------------


@pytest.mark.parametrize(
    "hours,expected",
    [(24, 1), (48, 2), (168, 7), (36, 2), (1, 1)],
    ids=["one_day", "two_days", "a_week", "part_day_rounds_up", "an_hour_is_still_a_day"],
)
def test_days_in_the_period(hours, expected):
    assert _days(START, START + timedelta(hours=hours)) == expected


# --- minutes a task takes out of the period -------------------------------


def test_a_task_inside_the_period_counts_in_full():
    task = _Task(START + timedelta(hours=2), START + timedelta(hours=4))

    assert _overlap_minutes(task, START, START + timedelta(days=1)) == 120


def test_a_task_that_starts_before_the_period_is_clipped():
    task = _Task(START - timedelta(hours=3), START + timedelta(hours=1))

    assert _overlap_minutes(task, START, START + timedelta(days=1)) == 60


def test_a_task_that_runs_past_the_period_is_clipped():
    end = START + timedelta(days=1)
    task = _Task(end - timedelta(minutes=30), end + timedelta(hours=5))

    assert _overlap_minutes(task, START, end) == 30


def test_a_task_outside_the_period_counts_for_nothing():
    task = _Task(START + timedelta(days=9), START + timedelta(days=9, hours=2))

    assert _overlap_minutes(task, START, START + timedelta(days=1)) == 0


# --- UC-07 2a: is there enough recorded history to plan from? -------------


def _summary(*, transactions: int, products: int = 1, stock: tuple = ("flour",)) -> BusinessSummary:
    line = ProfitLine(income=0, expenses=0, transactions=transactions)
    return BusinessSummary(
        period_start=START,
        period_end=START + timedelta(days=7),
        profit=ProfitabilitySummary(
            overall=line,
            general=ProfitLine(),
            by_order=(),
            by_product=tuple(
                ProductProfit(uuid.uuid4(), f"product {n}", ProfitLine())
                for n in range(products)
            ),
        ),
        stock=stock,
        capacity=CapacityPosition(480, 7, 0),
    )


def test_a_full_ledger_is_sufficient_to_plan_from():
    assert _summary(transactions=MIN_LEDGER_TRANSACTIONS).is_sufficient is True


def test_too_few_transactions_is_not_sufficient():
    assert _summary(transactions=MIN_LEDGER_TRANSACTIONS - 1).is_sufficient is False


def test_no_product_history_is_not_sufficient():
    assert _summary(transactions=50, products=0).is_sufficient is False


def test_no_stock_records_is_not_sufficient():
    assert _summary(transactions=50, stock=()).is_sufficient is False
