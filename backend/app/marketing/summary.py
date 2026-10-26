"""Validated business summary for campaign planning (BAKE-25; UC-07 step 2, AR-04).

``MarketingService.summarise_business`` assembles the figures a campaign plan
has to be grounded in: what the bakery actually earned, what ingredients it
can actually spare, and how much baking time is actually free.

Every number here is read from stored records. Nothing is generated, guessed
or rounded on the way through, and the language model never contributes a
figure -- it only ever reads the finished summary. That is the point of the
card, and it is a structural property rather than a promise: this module has
no access to ``LLMGateway`` or to any agent.

Two choices worth knowing:

- Profit comes from ``ProfitabilityService`` (BAKE-34) rather than from a
  second calculation over the same ledger. The whole ``ProfitabilitySummary``
  is embedded, so the owner's finance page and a campaign plan can never show
  different profit for the same period.
- Stock is *available* stock, not stock on hand, and capacity is *free*
  capacity, not daily capacity. A promotion is only realistic if the
  ingredients and the oven time are genuinely spare, so both figures subtract
  what released production plans have already committed.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.inventory_production import InventoryService
from app.pricing_bookkeeping.profitability import ProfitabilityService, ProfitabilitySummary
from app.shared.records import (
    BakerySettings,
    InventoryItem,
    ProductionSchedule,
    ProductionTask,
)
from app.shared.store import ValidatedRecordStore

# UC-07 2a: below this many recorded transactions in the period, the agent is
# told the data is thin and must produce a general suggestion carrying a
# low-confidence notice instead of a data-driven plan. A documented threshold,
# not a tuned one; it belongs with the other Stage 1 assumptions (report §15).
MIN_LEDGER_TRANSACTIONS = 5


@dataclass(frozen=True)
class StockPosition:
    """One ingredient, as it stands for the period being planned."""

    item_id: uuid.UUID
    ingredient_name: str
    unit: str
    on_hand: Decimal
    # On hand less what released production plans have already committed;
    # negative if the item is over-committed.
    available: Decimal


@dataclass(frozen=True)
class CapacityPosition:
    """Baking time in the period, and how much of it is already spoken for."""

    daily_capacity_minutes: int
    days: int
    committed_minutes: int

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

    period_start: datetime
    period_end: datetime
    profit: ProfitabilitySummary
    stock: tuple[StockPosition, ...]
    capacity: CapacityPosition

    @property
    def is_sufficient(self) -> bool:
        """Whether there is enough recorded history to plan from (UC-07 2a).

        False sends the agent down the low-confidence branch of the activity
        diagram: it may still suggest something general, but it must say the
        data was thin.
        """
        return (
            self.profit.overall.transactions >= MIN_LEDGER_TRANSACTIONS
            and bool(self.profit.by_product)
            and bool(self.stock)
        )

    @property
    def most_profitable_products(self) -> tuple:
        """Products ranked by profit, highest first (BAKE-34 sorts them)."""
        return self.profit.by_product


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
        self, *, period_start: datetime, period_end: datetime
    ) -> BusinessSummary:
        """Assemble the validated business summary for one period.

        ``period_start`` is inclusive and ``period_end`` exclusive, matching
        ``ProfitabilityService.summary``. Both bounds need a time zone.
        """
        for bound in (period_start, period_end):
            if bound.utcoffset() is None:
                raise ValueError("period bounds need a time zone")
        if period_end <= period_start:
            raise ValueError("period_end must be after period_start")

        profit = self.profitability.summary(period_start=period_start, period_end=period_end)
        with self.store.unit_of_work() as session:
            stock = self._stock(session)
            capacity = self._capacity(session, period_start, period_end)

        return BusinessSummary(
            period_start=period_start,
            period_end=period_end,
            profit=profit,
            stock=stock,
            capacity=capacity,
        )

    def _stock(self, session: Session) -> tuple[StockPosition, ...]:
        """Every ingredient with what is genuinely spare, by name."""
        items = list(
            session.scalars(select(InventoryItem).order_by(InventoryItem.ingredient_name))
        )
        if not items:
            return ()
        available = self.inventory.available_stock(session, [item.item_id for item in items])
        return tuple(
            StockPosition(
                item_id=item.item_id,
                ingredient_name=item.ingredient_name,
                unit=item.unit,
                on_hand=item.quantity_on_hand,
                available=available.get(item.item_id, item.quantity_on_hand),
            )
            for item in items
        )

    @staticmethod
    def _capacity(session: Session, start: datetime, end: datetime) -> CapacityPosition:
        """Daily capacity over the period, less minutes released plans hold."""
        settings = session.scalars(select(BakerySettings)).one()
        tasks = session.scalars(
            select(ProductionTask)
            .join(
                ProductionSchedule,
                and_(
                    ProductionSchedule.schedule_id == ProductionTask.schedule_id,
                    ProductionSchedule.released_version == ProductionTask.schedule_version,
                ),
            )
            .where(ProductionTask.start_at < end, ProductionTask.end_at > start)
        )
        committed = sum(_overlap_minutes(task, start, end) for task in tasks)
        return CapacityPosition(
            daily_capacity_minutes=settings.default_daily_capacity_minutes,
            days=_days(start, end),
            committed_minutes=committed,
        )


def _days(start: datetime, end: datetime) -> int:
    """Days in the period, rounded up, so a part-day still offers capacity."""
    return max(math.ceil((end - start) / timedelta(days=1)), 1)


def _overlap_minutes(task: ProductionTask, start: datetime, end: datetime) -> int:
    """Minutes of ``task`` that fall inside the period, rounded up."""
    begins = max(task.start_at, start)
    finishes = min(task.end_at, end)
    if finishes <= begins:
        return 0
    return math.ceil((finishes - begins).total_seconds() / 60)
