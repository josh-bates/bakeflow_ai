"""BAKE-34: profit figures match a fixture ledger (UC-06 step 4)."""

import uuid
from decimal import Decimal

from app.pricing_bookkeeping.profitability import LedgerFact, ProfitLine, summarise

CUPCAKE = uuid.UUID(int=1)
CAKE = uuid.UUID(int=2)
ORDER_A = uuid.UUID(int=10)  # 24 vanilla cupcakes
ORDER_B = uuid.UUID(int=11)  # one celebration cake
NAMES = {CUPCAKE: "Vanilla cupcake", CAKE: "Vanilla celebration cake"}


def income(amount, order=None, product=None):
    return LedgerFact("Income", Decimal(amount), order, product)


def expense(amount, order=None, product=None):
    return LedgerFact("Expense", Decimal(amount), order, product)


# Worked by hand:
#   order A: income 94.23, ingredients 14.96, boxes 6.00   -> profit 73.27
#   order B: income 120.00, ingredients 18.40               -> profit 101.60
#   general: rent 50.00 (no order, no product)
#   overall: income 214.23, expenses 89.36                  -> profit 124.87
FIXTURE_LEDGER = [
    income("94.23", ORDER_A, CUPCAKE),
    expense("14.96", ORDER_A, CUPCAKE),
    expense("6.00", ORDER_A),
    income("120.00", ORDER_B, CAKE),
    expense("18.40", ORDER_B, CAKE),
    expense("50.00"),
]


def test_overall_profit_matches_the_fixture_ledger():
    summary = summarise(FIXTURE_LEDGER, NAMES)

    assert summary.overall.income == Decimal("214.23")
    assert summary.overall.expenses == Decimal("89.36")
    assert summary.overall.profit == Decimal("124.87")
    assert summary.overall.transactions == 6
    assert summary.overall.margin == Decimal("0.5829")  # 124.87 / 214.23 = 0.58288...


def test_profit_by_order_matches_the_fixture_ledger():
    summary = summarise(FIXTURE_LEDGER, NAMES)

    by_order = {line.order_id: line.line for line in summary.by_order}
    assert by_order[ORDER_A].profit == Decimal("73.27")
    assert by_order[ORDER_A].expenses == Decimal("20.96")
    assert by_order[ORDER_B].profit == Decimal("101.60")
    # Highest profit first.
    assert [line.order_id for line in summary.by_order] == [ORDER_B, ORDER_A]


def test_profit_by_product_uses_only_transactions_for_that_product():
    summary = summarise(FIXTURE_LEDGER, NAMES)

    by_product = {line.name: line.line for line in summary.by_product}
    # The 6.00 boxes were recorded against order A only, not the product.
    assert by_product["Vanilla cupcake"].profit == Decimal("79.27")
    assert by_product["Vanilla celebration cake"].profit == Decimal("101.60")
    assert by_product["Vanilla celebration cake"].margin == Decimal("0.8467")


def test_general_expenses_count_only_in_the_overall_totals():
    summary = summarise(FIXTURE_LEDGER, NAMES)

    assert summary.general.expenses == Decimal("50.00")
    assert summary.general.income == Decimal("0.00")
    assert sum(line.line.expenses for line in summary.by_order) == Decimal("39.36")


def test_money_stays_exact_to_the_cent():
    # 0.10 + 0.20 is exactly 0.30 in Decimal (it is not in binary floating point).
    summary = summarise([income("0.10"), income("0.20")], NAMES)
    assert summary.overall.income == Decimal("0.30")
    assert str(summary.overall.profit) == "0.30"


def test_losses_and_no_income_are_reported_plainly():
    loss = summarise([income("10.00", ORDER_A), expense("15.50", ORDER_A)], NAMES)
    assert loss.by_order[0].line.profit == Decimal("-5.50")
    assert loss.by_order[0].line.margin == Decimal("-0.5500")

    costs_only = summarise([expense("8.00", ORDER_A)], NAMES)
    assert costs_only.by_order[0].line.margin is None


def test_empty_ledger_gives_zero_figures():
    summary = summarise([], NAMES)
    assert summary.overall == ProfitLine()
    assert summary.overall.profit == Decimal("0.00")
    assert summary.by_order == summary.by_product == ()


def test_unknown_product_is_labelled_rather_than_dropped():
    stray = uuid.UUID(int=99)
    summary = summarise([income("5.00", product=stray)], NAMES)
    assert summary.by_product[0].name == "Unknown product"
    assert summary.by_product[0].line.income == Decimal("5.00")
