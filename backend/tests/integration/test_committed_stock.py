"""BAKE-19: stock committed to other approved orders (IP-02), on PostgreSQL.

Jira tests: two orders sharing flour -> the second shows a shortage; a
multi-task order is counted once; a revised schedule is not double-counted.
Architecture T-IP02-RACE: two guarded plan saves cannot both use the same
stock. Commitments come from each schedule's released ``requirements_snapshot``
(architecture §4.3 "Availability"), not from separate reservation records.
"""

import threading
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.inventory_production import InventoryService
from app.shared.records import (
    Order,
    OrderItem,
    OrderStatus,
    ProductionSchedule,
    ProductionScheduleVersion,
    ProductionTask,
    ShoppingList,
)

PLAN_DAY = date(2026, 10, 1)
DAY_START = datetime(2026, 9, 30, 20, 0, tzinfo=UTC)  # 06:00 on 1 Oct in Sydney
REQUIRED_AT = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)


@pytest.fixture
def inventory(store) -> InventoryService:
    return InventoryService(store)


def confirmed_order(store, seeded, cupcakes=24):
    """A Confirmed vanilla cupcake order; 24 cupcakes need 600 g of the 1,000 g flour."""
    with store.unit_of_work() as session:
        order = Order(
            customer_id=seeded.customer_id,
            required_at=REQUIRED_AT,
            status=OrderStatus.CONFIRMED,
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=cupcakes)],
        )
        return store.commit(session, order, actor_id=seeded.owner_id, action="test.order")


def flour(seeded, grams):
    return {str(seeded.items["Flour"]): f"{grams}.000"}


def add_plan_version(store, session, seeded, schedule, needs, *, tasks_per_order=1):
    """Save one planned version: its requirements snapshot and its tasks."""
    session.add(
        ProductionScheduleVersion(
            schedule_id=schedule.schedule_id,
            version=schedule.version,
            start_date=PLAN_DAY,
            end_date=PLAN_DAY,
            daily_capacity_minutes=240,
            requirements_snapshot={str(ref.record_id): grams for ref, grams in needs.items()},
            source_order_refs=[ref.to_json() for ref in needs],
        )
    )
    start = DAY_START
    for ref in needs:
        for kind in ("bake", "finish")[:tasks_per_order]:
            task = ProductionTask(
                schedule_id=schedule.schedule_id,
                schedule_version=schedule.version,
                order_id=ref.record_id,
                task_kind=kind,
                start_at=start,
                end_at=start + timedelta(minutes=30),
            )
            store.commit(session, task, actor_id=seeded.owner_id, action="test.task")
            start += timedelta(minutes=30)


def draft_plan(store, seeded, needs, **kwargs):
    """A schedule whose version 1 is planned but not released."""
    with store.unit_of_work() as session:
        schedule = ProductionSchedule(created_by=seeded.owner_id)
        store.commit(session, schedule, actor_id=seeded.owner_id, action="test.schedule")
        add_plan_version(store, session, seeded, schedule, needs, **kwargs)
        return schedule.schedule_id


def release(store, seeded, schedule_id, version):
    """Point the schedule at ``version`` as its effective released plan."""
    with store.unit_of_work() as session:
        schedule = store.load(session, ProductionSchedule, schedule_id)
        schedule.released_version = version
        store.commit(
            session, schedule, actor_id=seeded.owner_id, action="test.release",
            expected_version=schedule.version,
        )


def released_plan(store, seeded, needs, **kwargs):
    schedule_id = draft_plan(store, seeded, needs, **kwargs)
    release(store, seeded, schedule_id, 1)
    return schedule_id


def revise_plan(store, seeded, schedule_id, needs):
    """Plan a new version; the released version stays effective until released."""
    with store.unit_of_work() as session:
        schedule = store.load(session, ProductionSchedule, schedule_id)
        store.commit(
            session, schedule, actor_id=seeded.owner_id, action="test.revise",
            expected_version=schedule.version, revise=True,
        )
        add_plan_version(store, session, seeded, schedule, needs)
        return schedule.version


def shortages_of(inventory, seeded, ref):
    return inventory.run_stock_check(
        ref.record_id, expected_order_version=ref.version, actor_id=seeded.owner_id
    ).shortages


def test_second_order_sharing_flour_shows_a_shortage(store, seeded, inventory):
    first, second = confirmed_order(store, seeded), confirmed_order(store, seeded)
    released_plan(store, seeded, {first: flour(seeded, 600)})

    result = inventory.run_stock_check(
        second.record_id, expected_order_version=second.version, actor_id=seeded.owner_id
    )

    # 1,000 g on hand, 600 g committed to the first order: 400 g left for 600 g.
    assert result.shortages == {seeded.items["Flour"]: Decimal("200.000")}
    with store.unit_of_work() as session:
        saved = session.get(ShoppingList, result.shopping_list_id)
        assert saved.shortages == {str(seeded.items["Flour"]): "200.000"}


def test_multi_task_order_is_counted_once(store, seeded, inventory):
    first, second = confirmed_order(store, seeded), confirmed_order(store, seeded)
    released_plan(store, seeded, {first: flour(seeded, 600)}, tasks_per_order=2)

    assert shortages_of(inventory, seeded, second) == {seeded.items["Flour"]: Decimal("200.000")}


def test_revised_schedule_is_not_double_counted(store, seeded, inventory):
    first, second = confirmed_order(store, seeded), confirmed_order(store, seeded)
    schedule_id = released_plan(store, seeded, {first: flour(seeded, 600)})
    version = revise_plan(store, seeded, schedule_id, {first: flour(seeded, 300)})

    # Until it is released, the revision commits nothing; version 1 still does.
    assert shortages_of(inventory, seeded, second) == {seeded.items["Flour"]: Decimal("200.000")}

    release(store, seeded, schedule_id, version)

    # Released, version 2 replaces version 1: 300 g committed, not 900 g.
    assert shortages_of(inventory, seeded, second) == {}


def test_unreleased_plan_commits_nothing(store, seeded, inventory):
    first, second = confirmed_order(store, seeded), confirmed_order(store, seeded)
    draft_plan(store, seeded, {first: flour(seeded, 600)})

    assert shortages_of(inventory, seeded, second) == {}


@pytest.mark.parametrize("status", [OrderStatus.COMPLETED, OrderStatus.CANCELLED])
def test_completed_or_cancelled_order_no_longer_commits_stock(store, seeded, inventory, status):
    first, second = confirmed_order(store, seeded), confirmed_order(store, seeded)
    released_plan(store, seeded, {first: flour(seeded, 600)})
    with store.unit_of_work() as session:
        order = store.load(session, Order, first.record_id)
        order.status = status
        store.commit(
            session, order, actor_id=seeded.owner_id, action="test.close",
            expected_version=first.version,
        )

    assert shortages_of(inventory, seeded, second) == {}


def test_rechecking_a_planned_order_does_not_count_it_twice(store, seeded, inventory):
    first = confirmed_order(store, seeded)
    released_plan(store, seeded, {first: flour(seeded, 600)})

    # Its own commitment is replaced by the recalculated need, not added to it.
    assert shortages_of(inventory, seeded, first) == {}


def test_plan_check_combines_its_orders_against_other_commitments(store, seeded, inventory):
    planned = confirmed_order(store, seeded, cupcakes=12)  # 300 g
    first, second = confirmed_order(store, seeded), confirmed_order(store, seeded)
    released_plan(store, seeded, {planned: flour(seeded, 300)})
    needs = {
        first.record_id: {seeded.items["Flour"]: Decimal("600.000")},
        second.record_id: {seeded.items["Flour"]: Decimal("600.000")},
    }

    with store.constraint_guard() as session:
        shortages = inventory.check_plan(session, needs)

    # 1,200 g for the two candidates against 1,000 g less 300 g committed.
    assert shortages == {seeded.items["Flour"]: Decimal("500.000")}


def test_two_guarded_plan_saves_cannot_both_use_the_same_flour(store, seeded, inventory):
    """T-IP02-RACE: each plan needs 600 g of the 1,000 g; only one is released."""
    orders = [confirmed_order(store, seeded), confirmed_order(store, seeded)]
    start = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def save_plan(ref):
        try:
            start.wait(timeout=5)
            with store.constraint_guard() as session:
                needs = {ref.record_id: {seeded.items["Flour"]: Decimal("600.000")}}
                if inventory.check_plan(session, needs):
                    outcomes.append("short")
                    return
                time.sleep(0.3)  # widen the race window
                schedule = ProductionSchedule(created_by=seeded.owner_id)
                store.commit(session, schedule, actor_id=seeded.owner_id, action="test.schedule")
                add_plan_version(store, session, seeded, schedule, {ref: flour(seeded, 600)})
                schedule.released_version = 1
                store.commit(
                    session, schedule, actor_id=seeded.owner_id, action="test.release",
                    expected_version=1,
                )
                outcomes.append("released")
        except BaseException as exc:  # surface thread failures in the test
            errors.append(exc)

    threads = [threading.Thread(target=save_plan, args=(ref,)) for ref in orders]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert sorted(outcomes) == ["released", "short"]
