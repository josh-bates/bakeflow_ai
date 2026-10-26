"""The business summary's figures come from stored records (BAKE-25, UC-07 step 2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.marketing import MIN_LEDGER_TRANSACTIONS, MarketingService
from app.shared.records import BakerySettings, InventoryItem, Transaction, TransactionKind

START = datetime(2026, 11, 1, tzinfo=UTC)
END = START + timedelta(days=7)


@pytest.fixture
def marketing(store) -> MarketingService:
    return MarketingService(store)


def record_transaction(
    store,
    seeded,
    *,
    kind: str,
    amount: str,
    reference: str,
    occurred_at: datetime,
    product_id: uuid.UUID | None = None,
) -> None:
    """One ledger entry, saved through the store like any other record."""
    with store.unit_of_work() as session:
        store.commit(
            session,
            Transaction(
                kind=kind,
                amount=Decimal(amount),
                source_type="manual",
                source_reference=reference,
                occurred_at=occurred_at,
                created_by=seeded.owner_id,
                product_id=product_id,
            ),
            actor_id=seeded.owner_id,
            action="transaction.record",
        )


# --- profit comes from the ledger, through BAKE-34 ------------------------


def test_profit_figures_match_the_recorded_ledger(store, marketing, seeded):
    product_id = next(iter(seeded.products.values()))
    record_transaction(
        store, seeded, kind=TransactionKind.INCOME, amount="120.00",
        reference="sale-1", occurred_at=START + timedelta(days=1), product_id=product_id,
    )
    record_transaction(
        store, seeded, kind=TransactionKind.INCOME, amount="80.50",
        reference="sale-2", occurred_at=START + timedelta(days=2), product_id=product_id,
    )
    record_transaction(
        store, seeded, kind=TransactionKind.EXPENSE, amount="45.25",
        reference="flour-invoice", occurred_at=START + timedelta(days=3),
    )

    summary = marketing.summarise_business(period_start=START, period_end=END)

    assert summary.profit.overall.income == Decimal("200.50")
    assert summary.profit.overall.expenses == Decimal("45.25")
    assert summary.profit.overall.profit == Decimal("155.25")
    assert summary.profit.overall.transactions == 3

    # The ingredient invoice has no product, so it is general, not the product's.
    assert summary.profit.general.expenses == Decimal("45.25")
    [product] = summary.profit.by_product
    assert product.product_id == product_id
    assert product.line.income == Decimal("200.50")
    assert product.line.expenses == Decimal("0.00")


def test_transactions_outside_the_period_are_excluded(store, marketing, seeded):
    record_transaction(
        store, seeded, kind=TransactionKind.INCOME, amount="500.00",
        reference="last-month", occurred_at=START - timedelta(days=1),
    )
    record_transaction(
        store, seeded, kind=TransactionKind.INCOME, amount="300.00",
        reference="next-month", occurred_at=END + timedelta(days=1),
    )
    record_transaction(
        store, seeded, kind=TransactionKind.INCOME, amount="60.00",
        reference="in-the-period", occurred_at=START + timedelta(days=2),
    )

    summary = marketing.summarise_business(period_start=START, period_end=END)

    assert summary.profit.overall.income == Decimal("60.00")
    assert summary.profit.overall.transactions == 1


# --- stock ----------------------------------------------------------------


def test_stock_lists_every_ingredient_with_what_is_available(store, marketing, seeded):
    summary = marketing.summarise_business(period_start=START, period_end=END)

    with store.unit_of_work() as session:
        stored = {
            item.item_id: item
            for item in session.scalars(select(InventoryItem))
        }

    assert {line.item_id for line in summary.stock} == set(stored)
    for line in summary.stock:
        item = stored[line.item_id]
        assert line.ingredient_name == item.ingredient_name
        assert line.unit == item.unit
        assert line.on_hand == item.quantity_on_hand
        # Nothing is committed to a released plan, so all of it is spare.
        assert line.available == item.quantity_on_hand


def test_stock_is_sorted_by_ingredient_name(marketing, seeded):
    summary = marketing.summarise_business(period_start=START, period_end=END)

    names = [line.ingredient_name for line in summary.stock]
    assert names == sorted(names)


# --- capacity -------------------------------------------------------------


def test_capacity_is_the_whole_period_when_nothing_is_scheduled(store, marketing, seeded):
    summary = marketing.summarise_business(period_start=START, period_end=END)

    with store.unit_of_work() as session:
        settings = session.scalars(select(BakerySettings)).one()

    assert summary.capacity.daily_capacity_minutes == settings.default_daily_capacity_minutes
    assert summary.capacity.days == 7
    assert summary.capacity.committed_minutes == 0
    assert summary.capacity.free_minutes == settings.default_daily_capacity_minutes * 7


# --- UC-07 2a -------------------------------------------------------------


def test_an_empty_ledger_is_not_enough_to_plan_from(marketing, seeded):
    summary = marketing.summarise_business(period_start=START, period_end=END)

    assert summary.profit.overall.transactions == 0
    assert summary.is_sufficient is False


def test_a_full_ledger_is_enough_to_plan_from(store, marketing, seeded):
    product_id = next(iter(seeded.products.values()))
    for number in range(MIN_LEDGER_TRANSACTIONS):
        record_transaction(
            store, seeded, kind=TransactionKind.INCOME, amount="20.00",
            reference=f"sale-{number}", occurred_at=START + timedelta(days=1),
            product_id=product_id,
        )

    summary = marketing.summarise_business(period_start=START, period_end=END)

    assert summary.is_sufficient is True


# --- the period itself ----------------------------------------------------


def test_period_bounds_need_a_time_zone(marketing):
    with pytest.raises(ValueError):
        marketing.summarise_business(
            period_start=datetime(2026, 11, 1), period_end=datetime(2026, 11, 8)
        )


def test_the_period_must_end_after_it_starts(marketing):
    with pytest.raises(ValueError):
        marketing.summarise_business(period_start=END, period_end=START)
