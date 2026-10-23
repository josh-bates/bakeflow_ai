"""Deterministic profitability by order and product for UC-06 step 4 (BAKE-34, AR-03).

``FinanceService`` recalculates profitability from the recorded ledger only:
income less expenses, per order, per product and overall. No figure comes
from an AI explanation, and an explanation failure (5a) never hides these
figures, because they are calculated before and without it.

Attribution follows the Stage 1 assumption that a transaction belongs to at
most one order and one product (report §15):

- an order's profit uses the transactions recorded against that order;
- a product's profit uses the transactions recorded against that product;
- a transaction with neither is a general income or expense, counted only in
  the overall totals.

So one transaction can appear under both its order and its product, and the
per-order and per-product lists are two views of the same ledger, not parts
that add up to each other.

Money is exact: amounts are stored to the cent, and sums of cents need no
rounding. The one rounded value is the margin (profit / income), rounded once
to four decimal places with ``ROUND_HALF_UP``, the same rule as quotations.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.pricing_bookkeeping.quotation import MARGIN_PLACES
from app.shared.records import Product, Transaction, TransactionKind
from app.shared.store import ValidatedRecordStore

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class LedgerFact:
    """The parts of a ledger transaction that profitability uses."""

    kind: str
    amount: Decimal
    order_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ProfitLine:
    """Income, expenses and profit for one order, one product, or the whole ledger."""

    income: Decimal = ZERO
    expenses: Decimal = ZERO
    transactions: int = 0

    @property
    def profit(self) -> Decimal:
        return self.income - self.expenses

    @property
    def margin(self) -> Decimal | None:
        """Profit as a share of income, or ``None`` when there is no income."""
        if self.income == 0:
            return None
        return (self.profit / self.income).quantize(MARGIN_PLACES, rounding=ROUND_HALF_UP)

    def add(self, fact: LedgerFact) -> ProfitLine:
        if fact.kind == TransactionKind.INCOME:
            return ProfitLine(self.income + fact.amount, self.expenses, self.transactions + 1)
        return ProfitLine(self.income, self.expenses + fact.amount, self.transactions + 1)


@dataclass(frozen=True)
class ProductProfit:
    product_id: uuid.UUID
    name: str
    line: ProfitLine


@dataclass(frozen=True)
class OrderProfit:
    order_id: uuid.UUID
    line: ProfitLine


@dataclass(frozen=True)
class ProfitabilitySummary:
    """UC-06 step 4 result. Lists are sorted by profit, highest first."""

    overall: ProfitLine
    general: ProfitLine
    by_order: tuple[OrderProfit, ...]
    by_product: tuple[ProductProfit, ...]
    period_start: datetime | None = None
    period_end: datetime | None = None


def summarise(
    facts: Iterable[LedgerFact],
    product_names: dict[uuid.UUID, str],
    *,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> ProfitabilitySummary:
    """Profit overall, per order and per product, from ledger facts alone."""
    overall = ProfitLine()
    general = ProfitLine()
    orders: dict[uuid.UUID, ProfitLine] = defaultdict(ProfitLine)
    products: dict[uuid.UUID, ProfitLine] = defaultdict(ProfitLine)
    for fact in facts:
        overall = overall.add(fact)
        if fact.order_id is not None:
            orders[fact.order_id] = orders[fact.order_id].add(fact)
        if fact.product_id is not None:
            products[fact.product_id] = products[fact.product_id].add(fact)
        if fact.order_id is None and fact.product_id is None:
            general = general.add(fact)

    def ranked(line: ProfitLine, key: uuid.UUID):
        return (-line.profit, str(key))

    return ProfitabilitySummary(
        overall=overall,
        general=general,
        by_order=tuple(
            OrderProfit(order_id, line)
            for order_id, line in sorted(orders.items(), key=lambda item: ranked(item[1], item[0]))
        ),
        by_product=tuple(
            ProductProfit(product_id, product_names.get(product_id, "Unknown product"), line)
            for product_id, line in sorted(products.items(), key=lambda item: ranked(item[1], item[0]))
        ),
        period_start=period_start,
        period_end=period_end,
    )


class ProfitabilityService:
    """Reads the ledger and summarises it (UC-06 step 4).

    Also the source of profit figures for the marketing business summary
    (BAKE-25), so both views use the same calculation.
    """

    def __init__(self, store: ValidatedRecordStore):
        self.store = store

    def summary(
        self, *, period_start: datetime | None = None, period_end: datetime | None = None
    ) -> ProfitabilitySummary:
        """Profitability for transactions with ``period_start <= occurred_at < period_end``.

        Either bound may be omitted. Bounds need a time zone.
        """
        for bound in (period_start, period_end):
            if bound is not None and bound.utcoffset() is None:
                raise ValueError("period bounds need a time zone")
        query = select(
            Transaction.kind, Transaction.amount, Transaction.order_id, Transaction.product_id
        )
        if period_start is not None:
            query = query.where(Transaction.occurred_at >= period_start)
        if period_end is not None:
            query = query.where(Transaction.occurred_at < period_end)
        with self.store.unit_of_work() as session:
            facts = [LedgerFact(*row) for row in session.execute(query)]
            names = dict(session.execute(select(Product.product_id, Product.name)).all())
        return summarise(facts, names, period_start=period_start, period_end=period_end)
