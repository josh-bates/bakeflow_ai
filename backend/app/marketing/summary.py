"""Validated business summary for campaign planning (BAKE-25; UC-07 step 2, AR-04).

``MarketingService.summarise_business`` assembles the figures a campaign plan
has to be grounded in: what the bakery has actually sold and earned, what
ingredients it can spare, and how much baking time is free on the days the
campaign would run.

Every number here is read from stored records. Nothing is generated or
guessed, and the language model never contributes a figure -- it only reads
the finished summary. That is structural rather than a promise: this module
imports no agent and no ``LLMGateway``.

Two periods, because they look in opposite directions
-----------------------------------------------------
The ledger looks back: what sold, what it earned, what it cost. Capacity and
stock look forward: what is free on the days being planned. One window cannot
serve both -- the last 30 days of free oven time is not a useful number, and
the next 7 days of sales history is empty. So the caller passes a history
period (timestamps, for the ledger) and planning dates (local dates, for
capacity).

The planning window is a range of **dates in the bakery's time zone**, the
same as ``PlanWindow`` in scheduling, so the days counted here are the days
the scheduler plans. Counting elapsed time instead would stretch a one-hour
window into a whole day of capacity, and would give a different answer for
the same week depending on daylight saving.

What the figures mean
---------------------
- Profit comes from ``ProfitabilityService`` (BAKE-34), embedded whole rather
  than recalculated, so the owner's finance page and a campaign plan cannot
  disagree about one ledger.
- Products are ranked by **units sold**, not by profit. An ``OrderPayment``
  records its order but usually no product, and ingredient invoices are
  general, so per-product profit is mostly income with the costs missing.
  Ranking by units is honest; ``overall`` and ``general`` sit beside it so a
  claim about profit uses the figure that has the costs in it.
- Stock is what is spare, and both figures come from **one read**, so a
  receipt committing mid-summary cannot give on-hand from before it and
  available from after it.
- Capacity excludes tasks of cancelled and completed orders, matching
  ``InventoryService.committed_needs``: a cancelled order releases its stock,
  so it must release its oven time too.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.inventory_production import InventoryService
from app.pricing_bookkeeping.profitability import (
    ProfitabilityService,
    ProfitabilitySummary,
    ProfitLine,
)
from app.shared.records import (
    BakerySettings,
    InventoryItem,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductionSchedule,
    ProductionTask,
)
from app.shared.store import ValidatedRecordStore

ZERO = Decimal("0.00")

# An order counts as sold once the customer has committed to it.
SOLD_STATUSES = frozenset(
    {
        OrderStatus.CONFIRMED,
        OrderStatus.SCHEDULED,
        OrderStatus.IN_PRODUCTION,
        OrderStatus.COMPLETED,
    }
)

# Closed orders hold neither stock nor oven time. Same set as
# InventoryService._CLOSED, so stock and capacity stay consistent.
CLOSED_STATUSES = frozenset({OrderStatus.CANCELLED, OrderStatus.COMPLETED})

# UC-07 2a: below this many recorded transactions in the history period, the
# agent is told the data is thin and must produce a general suggestion with a
# low-confidence notice. A documented assumption, not a tuned threshold
# (report §15).
MIN_LEDGER_TRANSACTIONS = 5


@dataclass(frozen=True)
class ProductDemand:
    """One product's real demand over the history period."""

    product_id: uuid.UUID
    name: str
    units_sold: int
    # Income recorded against this product, where the ledger has any. Usually
    # incomplete, because an OrderPayment names its order and not a product.
    income: Decimal = ZERO


@dataclass(frozen=True)
class StockPosition:
    """One ingredient, as it stands for the days being planned."""

    item_id: uuid.UUID
    ingredient_name: str
    unit: str
    on_hand: Decimal
    # On hand less what released plans of active orders commit; negative if
    # the item is over-committed.
    available: Decimal


@dataclass(frozen=True)
class CapacityPosition:
    """Baking time over the planning dates, and how much is already spoken for."""

    daily_capacity_minutes: int
    start_date: date
    end_date: date
    committed_minutes: int

    @property
    def days(self) -> int:
        """Whole local days, inclusive of both ends, as the scheduler counts them."""
        return (self.end_date - self.start_date).days + 1

    @property
    def total_minutes(self) -> int:
        return self.daily_capacity_minutes * self.days

    @property
    def free_minutes(self) -> int:
        """Never negative: an over-committed period simply has no room."""
        return max(self.total_minutes - self.committed_minutes, 0)


@dataclass(frozen=True)
class BusinessSummary:
    """What UC-07 step 2 hands to the Marketing Agent. Stored records only."""

    history_start: datetime
    history_end: datetime
    planning_start: date
    planning_end: date
    profit: ProfitabilitySummary
    top_products_by_units: tuple[ProductDemand, ...]
    stock: tuple[StockPosition, ...]
    capacity: CapacityPosition

    @property
    def overall(self) -> ProfitLine:
        """The bakery's income, expenses and profit: the figure with costs in it."""
        return self.profit.overall

    @property
    def general(self) -> ProfitLine:
        """Income and expenses belonging to no order or product (e.g. flour invoices)."""
        return self.profit.general

    @property
    def is_sufficient(self) -> bool:
        """Whether there is enough recorded history to plan from (UC-07 2a).

        False sends the agent down the low-confidence branch of the activity
        diagram: it may still suggest something general, but it must say the
        data was thin. Measured on sales and ledger activity, not on
        per-product profit, which is thin even for a busy bakery.
        """
        return (
            self.profit.overall.transactions >= MIN_LEDGER_TRANSACTIONS
            and bool(self.top_products_by_units)
            and bool(self.stock)
        )


@dataclass
class MarketingService:
    """Deterministic marketing data (architecture §6.1).

    Supplies the Marketing Agent with validated figures; it never calls the
    agent and never reaches the language model.
    """

    store: ValidatedRecordStore
    inventory: InventoryService = field(default=None)  # type: ignore[assignment]
    profitability: ProfitabilityService = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.inventory is None:
            self.inventory = InventoryService(self.store)
        if self.profitability is None:
            self.profitability = ProfitabilityService(self.store)

    def summarise_business(
        self,
        *,
        history_start: datetime,
        history_end: datetime,
        planning_start: date,
        planning_end: date,
    ) -> BusinessSummary:
        """Assemble the validated business summary.

        ``history_start``/``history_end`` bound the ledger and the sales
        history: timestamps, start inclusive and end exclusive, matching
        ``ProfitabilityService.summary``. ``planning_start``/``planning_end``
        are the campaign's days as local dates in the bakery's time zone,
        both ends inclusive, matching ``PlanWindow``.
        """
        for bound in (history_start, history_end):
            if bound.utcoffset() is None:
                raise ValueError("history bounds need a time zone")
        if history_end <= history_start:
            raise ValueError("history_end must be after history_start")
        if planning_end < planning_start:
            raise ValueError("planning_end must not be before planning_start")

        profit = self.profitability.summary(
            period_start=history_start, period_end=history_end
        )
        with self.store.unit_of_work() as session:
            demand = self._demand(session, profit, history_start, history_end)
            stock = self._stock(session)
            capacity = self._capacity(session, planning_start, planning_end)

        return BusinessSummary(
            history_start=history_start,
            history_end=history_end,
            planning_start=planning_start,
            planning_end=planning_end,
            profit=profit,
            top_products_by_units=demand,
            stock=stock,
            capacity=capacity,
        )

    @staticmethod
    def _demand(
        session: Session,
        profit: ProfitabilitySummary,
        start: datetime,
        end: datetime,
    ) -> tuple[ProductDemand, ...]:
        """Units sold per product, highest first, from the orders themselves.

        Counted from ``OrderItem`` rather than the ledger, because an
        ``OrderPayment`` records its order and not the products in it.
        """
        rows = session.execute(
            select(OrderItem.product_id, func.sum(OrderItem.quantity))
            .join(Order, Order.order_id == OrderItem.order_id)
            .where(
                Order.status.in_(list(SOLD_STATUSES)),
                Order.created_at >= start,
                Order.created_at < end,
            )
            .group_by(OrderItem.product_id)
        ).all()
        if not rows:
            return ()
        names = dict(session.execute(select(Product.product_id, Product.name)).all())
        income = {line.product_id: line.line.income for line in profit.by_product}
        demand = [
            ProductDemand(
                product_id=product_id,
                name=names.get(product_id, "Unknown product"),
                units_sold=int(units),
                income=income.get(product_id, ZERO),
            )
            for product_id, units in rows
        ]
        demand.sort(key=lambda product: (-product.units_sold, product.name))
        return tuple(demand)

    def _stock(self, session: Session) -> tuple[StockPosition, ...]:
        """Every ingredient with what is genuinely spare, from one read."""
        items = list(
            session.scalars(select(InventoryItem).order_by(InventoryItem.ingredient_name))
        )
        if not items:
            return ()
        # Both figures come from these same rows: a receipt committing between
        # two reads cannot give on-hand from before it and available from after.
        committed = self.inventory.committed_needs(session)
        return tuple(
            StockPosition(
                item_id=item.item_id,
                ingredient_name=item.ingredient_name,
                unit=item.unit,
                on_hand=item.quantity_on_hand,
                available=item.quantity_on_hand - committed.get(item.item_id, Decimal(0)),
            )
            for item in items
        )

    @staticmethod
    def _capacity(session: Session, start_date: date, end_date: date) -> CapacityPosition:
        """Daily capacity over the planning dates, less minutes active plans hold."""
        settings = session.scalars(select(BakerySettings)).one()
        zone = ZoneInfo(settings.time_zone)
        start = datetime.combine(start_date, time(0), zone).astimezone(UTC)
        end = datetime.combine(end_date + timedelta(days=1), time(0), zone).astimezone(UTC)

        tasks = session.scalars(
            select(ProductionTask)
            .join(
                ProductionSchedule,
                and_(
                    ProductionSchedule.schedule_id == ProductionTask.schedule_id,
                    ProductionSchedule.released_version == ProductionTask.schedule_version,
                ),
            )
            .join(Order, Order.order_id == ProductionTask.order_id)
            .where(
                ProductionTask.start_at < end,
                ProductionTask.end_at > start,
                Order.status.not_in(list(CLOSED_STATUSES)),
            )
        )
        committed = sum(_overlap_minutes(task, start, end) for task in tasks)
        return CapacityPosition(
            daily_capacity_minutes=settings.default_daily_capacity_minutes,
            start_date=start_date,
            end_date=end_date,
            committed_minutes=committed,
        )


def _overlap_minutes(task: ProductionTask, start: datetime, end: datetime) -> int:
    """Minutes of ``task`` that fall inside the window, rounded up."""
    begins = max(task.start_at, start)
    finishes = min(task.end_at, end)
    if finishes <= begins:
        return 0
    return math.ceil((finishes - begins).total_seconds() / 60)
