"""BAKE-34: profitability is recalculated from the recorded ledger, on PostgreSQL."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.pricing_bookkeeping.profitability import ProfitabilityService
from app.shared.records import Order, OrderItem, OrderStatus, Transaction

OCTOBER = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def profitability(store) -> ProfitabilityService:
    return ProfitabilityService(store)


def record(store, seeded, reference, kind, amount, *, order_id=None, product_id=None, days=0):
    with store.unit_of_work() as session:
        store.commit(
            session,
            Transaction(
                kind=kind, amount=Decimal(amount), source_type="Invoice",
                source_reference=reference, occurred_at=OCTOBER + timedelta(days=days),
                order_id=order_id, product_id=product_id, created_by=seeded.owner_id,
            ),
            actor_id=seeded.owner_id, action="test.transaction",
        )


def cupcake_order(store, seeded):
    with store.unit_of_work() as session:
        order = Order(
            customer_id=seeded.customer_id, required_at=OCTOBER + timedelta(days=30),
            status=OrderStatus.CONFIRMED,
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=24)],
        )
        return store.commit(session, order, actor_id=seeded.owner_id, action="test.order").record_id


def test_profit_is_recalculated_from_recorded_transactions(store, seeded, profitability):
    cupcakes = seeded.products["Vanilla cupcake"]
    order = cupcake_order(store, seeded)
    record(store, seeded, "R-1", "Income", "94.23", order_id=order, product_id=cupcakes)
    record(store, seeded, "INV-1", "Expense", "14.96", order_id=order, product_id=cupcakes)
    record(store, seeded, "INV-2", "Expense", "50.00")

    summary = profitability.summary()

    assert summary.overall.profit == Decimal("29.27")
    assert summary.general.expenses == Decimal("50.00")
    [order_line] = summary.by_order
    assert (order_line.order_id, order_line.line.profit) == (order, Decimal("79.27"))
    [product_line] = summary.by_product
    assert (product_line.name, product_line.line.margin) == ("Vanilla cupcake", Decimal("0.8412"))


def test_new_transactions_change_the_next_summary(store, seeded, profitability):
    record(store, seeded, "R-1", "Income", "40.00")
    assert profitability.summary().overall.profit == Decimal("40.00")

    record(store, seeded, "INV-1", "Expense", "15.00")
    assert profitability.summary().overall.profit == Decimal("25.00")


def test_period_includes_its_start_and_excludes_its_end(store, seeded, profitability):
    record(store, seeded, "R-1", "Income", "10.00", days=0)
    record(store, seeded, "R-2", "Income", "20.00", days=7)
    record(store, seeded, "R-3", "Income", "40.00", days=14)

    week_two = profitability.summary(period_start=OCTOBER + timedelta(days=7),
                                     period_end=OCTOBER + timedelta(days=14))
    assert week_two.overall.income == Decimal("20.00")


def test_period_bounds_need_a_time_zone(profitability):
    with pytest.raises(ValueError):
        profitability.summary(period_start=datetime(2026, 10, 1))
