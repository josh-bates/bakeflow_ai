"""The business summary's figures come from stored records (BAKE-25, UC-07 step 2)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.marketing import MIN_LEDGER_TRANSACTIONS, MarketingService
from app.shared.records import (
    BakerySettings,
    InventoryItem,
    Order,
    OrderItem,
    OrderStatus,
    ProductionSchedule,
    ProductionScheduleVersion,
    ProductionTask,
    Transaction,
    TransactionKind,
)

# The ledger and the sales history look back.
HISTORY_START = datetime(2026, 10, 1, tzinfo=UTC)
HISTORY_END = datetime(2026, 11, 1, tzinfo=UTC)
ORDERED_AT = datetime(2026, 10, 15, tzinfo=UTC)
PAID_AT = datetime(2026, 10, 16, tzinfo=UTC)

# Capacity looks forward: local dates in the bakery's time zone, both included.
PLANNING_START = date(2026, 11, 2)
PLANNING_END = date(2026, 11, 8)
PLANNING_DAYS = 7
# Inside the planning window once converted to UTC (Sydney is UTC+11 in November).
TASK_START = datetime(2026, 11, 3, tzinfo=UTC)

REQUIRED_AT = datetime(2026, 11, 5, tzinfo=UTC)


@pytest.fixture
def marketing(store) -> MarketingService:
    return MarketingService(store)


def summarise(marketing) -> object:
    return marketing.summarise_business(
        history_start=HISTORY_START,
        history_end=HISTORY_END,
        planning_start=PLANNING_START,
        planning_end=PLANNING_END,
    )


def order(
    store,
    seeded,
    *,
    product: str = "Vanilla cupcake",
    quantity: int = 12,
    status: str = OrderStatus.CONFIRMED,
    created_at: datetime = ORDERED_AT,
):
    """One order, saved through the store like any other record."""
    with store.unit_of_work() as session:
        record = Order(
            customer_id=seeded.customer_id,
            required_at=REQUIRED_AT,
            status=status,
            created_at=created_at,
            items=[OrderItem(product_id=seeded.products[product], quantity=quantity)],
        )
        return store.commit(session, record, actor_id=seeded.owner_id, action="test.order")


def payment(store, seeded, *, order_id: uuid.UUID, amount: str, reference: str, occurred_at=PAID_AT):
    """An OrderPayment: names its order, and usually no product (ledger.py)."""
    ledger_entry(
        store, seeded, kind=TransactionKind.INCOME, amount=amount,
        source_type="OrderPayment", reference=reference,
        occurred_at=occurred_at, order_id=order_id,
    )


def ledger_entry(
    store,
    seeded,
    *,
    kind: str,
    amount: str,
    source_type: str,
    reference: str,
    occurred_at: datetime,
    order_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
) -> None:
    with store.unit_of_work() as session:
        store.commit(
            session,
            Transaction(
                kind=kind,
                amount=Decimal(amount),
                source_type=source_type,
                source_reference=reference,
                occurred_at=occurred_at,
                created_by=seeded.owner_id,
                order_id=order_id,
                product_id=product_id,
            ),
            actor_id=seeded.owner_id,
            action="transaction.record",
        )


def released_plan(store, seeded, *, order_ref, needs_grams: int, minutes: int = 60):
    """A released schedule holding one order's stock and oven time."""
    with store.unit_of_work() as session:
        schedule = ProductionSchedule(created_by=seeded.owner_id)
        store.commit(session, schedule, actor_id=seeded.owner_id, action="test.schedule")
        version = ProductionScheduleVersion(
            schedule_id=schedule.schedule_id,
            version=schedule.version,
            start_date=PLANNING_START,
            end_date=PLANNING_END,
            daily_capacity_minutes=240,
            requirements_snapshot={
                str(order_ref.record_id): {str(seeded.items["Flour"]): f"{needs_grams}.000"}
            },
            source_order_refs=[order_ref.to_json()],
        )
        store.commit(session, version, actor_id=seeded.owner_id, action="test.version")
        store.commit(
            session,
            ProductionTask(
                schedule_id=schedule.schedule_id,
                schedule_version=schedule.version,
                order_id=order_ref.record_id,
                task_kind="bake",
                start_at=TASK_START,
                end_at=TASK_START + timedelta(minutes=minutes),
            ),
            actor_id=seeded.owner_id,
            action="test.task",
        )
        schedule_id = schedule.schedule_id
    with store.unit_of_work() as session:
        schedule = store.load(session, ProductionSchedule, schedule_id)
        schedule.released_version = 1
        store.commit(
            session, schedule, actor_id=seeded.owner_id, action="test.release",
            expected_version=schedule.version,
        )


def cancel(store, seeded, order_ref) -> None:
    with store.unit_of_work() as session:
        record = store.load(session, Order, order_ref.record_id)
        record.status = OrderStatus.CANCELLED
        store.commit(
            session, record, actor_id=seeded.owner_id, action="test.cancel",
            expected_version=record.version,
        )


# --- the ledger -----------------------------------------------------------


def test_profit_figures_match_the_recorded_ledger(store, marketing, seeded):
    placed = order(store, seeded)
    payment(store, seeded, order_id=placed.record_id, amount="120.00", reference="pay-1")
    ledger_entry(
        store, seeded, kind=TransactionKind.EXPENSE, amount="45.25",
        source_type="Invoice", reference="flour-invoice", occurred_at=PAID_AT,
    )

    summary = summarise(marketing)

    assert summary.overall.income == Decimal("120.00")
    assert summary.overall.expenses == Decimal("45.25")
    assert summary.overall.profit == Decimal("74.75")
    # The flour invoice belongs to no order and no product.
    assert summary.general.expenses == Decimal("45.25")
    # The payment belongs to its order, not to a product.
    assert summary.profit.by_product == ()
    assert len(summary.profit.by_order) == 1


def test_transactions_outside_the_history_period_are_excluded(store, marketing, seeded):
    placed = order(store, seeded)
    payment(
        store, seeded, order_id=placed.record_id, amount="500.00",
        reference="too-early", occurred_at=HISTORY_START - timedelta(days=1),
    )
    payment(
        store, seeded, order_id=placed.record_id, amount="300.00",
        reference="too-late", occurred_at=HISTORY_END + timedelta(days=1),
    )
    payment(store, seeded, order_id=placed.record_id, amount="60.00", reference="in-period")

    summary = summarise(marketing)

    assert summary.overall.income == Decimal("60.00")
    assert summary.overall.transactions == 1


# --- what actually sold ---------------------------------------------------


def test_products_are_ranked_by_units_sold(store, marketing, seeded):
    names = list(seeded.products)
    order(store, seeded, product=names[0], quantity=4)
    order(store, seeded, product=names[1], quantity=30)

    summary = summarise(marketing)

    assert [(line.name, line.units_sold) for line in summary.top_products_by_units] == [
        (names[1], 30),
        (names[0], 4),
    ]


def test_drafts_and_cancelled_orders_do_not_count_as_sales(store, marketing, seeded):
    order(store, seeded, quantity=5, status=OrderStatus.DRAFT)
    cancelled = order(store, seeded, quantity=7)
    cancel(store, seeded, cancelled)
    order(store, seeded, quantity=3)

    summary = summarise(marketing)

    [sold] = summary.top_products_by_units
    assert sold.units_sold == 3


def test_orders_outside_the_history_period_are_excluded(store, marketing, seeded):
    order(store, seeded, quantity=99, created_at=HISTORY_START - timedelta(days=1))

    summary = summarise(marketing)

    assert summary.top_products_by_units == ()


# --- UC-07 2a -------------------------------------------------------------


def test_real_order_payments_are_enough_to_plan_from(store, marketing, seeded):
    """Sufficiency cannot depend on per-product profit: payments name orders."""
    placed = order(store, seeded)
    for number in range(MIN_LEDGER_TRANSACTIONS):
        payment(
            store, seeded, order_id=placed.record_id,
            amount="20.00", reference=f"pay-{number}",
        )

    summary = summarise(marketing)

    assert summary.profit.by_product == ()
    assert summary.is_sufficient is True


def test_an_empty_ledger_is_not_enough_to_plan_from(marketing, seeded):
    summary = summarise(marketing)

    assert summary.overall.transactions == 0
    assert summary.is_sufficient is False


# --- stock ----------------------------------------------------------------


def test_stock_shows_what_released_plans_commit(store, marketing, seeded):
    placed = order(store, seeded)
    released_plan(store, seeded, order_ref=placed, needs_grams=600)

    summary = summarise(marketing)

    with store.unit_of_work() as session:
        stored = {item.item_id: item for item in session.scalars(select(InventoryItem))}

    assert {line.item_id for line in summary.stock} == set(stored)
    flour = next(line for line in summary.stock if line.item_id == seeded.items["Flour"])
    assert flour.on_hand == stored[flour.item_id].quantity_on_hand
    assert flour.available == flour.on_hand - Decimal("600.000")


def test_stock_is_sorted_by_ingredient_name(marketing, seeded):
    summary = summarise(marketing)

    names = [line.ingredient_name for line in summary.stock]
    assert names == sorted(names)


# --- capacity -------------------------------------------------------------


def test_capacity_covers_the_planning_window_when_nothing_is_scheduled(
    store, marketing, seeded
):
    summary = summarise(marketing)

    with store.unit_of_work() as session:
        settings = session.scalars(select(BakerySettings)).one()

    assert summary.capacity.daily_capacity_minutes == settings.default_daily_capacity_minutes
    assert summary.capacity.days == PLANNING_DAYS
    assert summary.capacity.committed_minutes == 0
    assert summary.capacity.free_minutes == (
        settings.default_daily_capacity_minutes * PLANNING_DAYS
    )


def test_a_released_task_takes_capacity(store, marketing, seeded):
    placed = order(store, seeded)
    released_plan(store, seeded, order_ref=placed, needs_grams=600, minutes=60)

    summary = summarise(marketing)

    assert summary.capacity.committed_minutes == 60


def test_a_cancelled_orders_tasks_do_not_take_capacity(store, marketing, seeded):
    """A cancelled order releases its stock, so it must release its oven time."""
    placed = order(store, seeded)
    released_plan(store, seeded, order_ref=placed, needs_grams=600, minutes=60)
    cancel(store, seeded, placed)

    summary = summarise(marketing)

    flour = next(line for line in summary.stock if line.item_id == seeded.items["Flour"])
    assert flour.available == flour.on_hand
    assert summary.capacity.committed_minutes == 0


# --- the periods themselves -----------------------------------------------


def test_history_bounds_need_a_time_zone(marketing):
    with pytest.raises(ValueError):
        marketing.summarise_business(
            history_start=datetime(2026, 10, 1),
            history_end=datetime(2026, 11, 1),
            planning_start=PLANNING_START,
            planning_end=PLANNING_END,
        )


def test_the_history_period_must_end_after_it_starts(marketing):
    with pytest.raises(ValueError):
        marketing.summarise_business(
            history_start=HISTORY_END,
            history_end=HISTORY_START,
            planning_start=PLANNING_START,
            planning_end=PLANNING_END,
        )


def test_the_planning_dates_must_not_run_backwards(marketing):
    with pytest.raises(ValueError):
        marketing.summarise_business(
            history_start=HISTORY_START,
            history_end=HISTORY_END,
            planning_start=PLANNING_END,
            planning_end=PLANNING_START,
        )
