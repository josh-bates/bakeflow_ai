"""BAKE-33: task progress and the once-only deduction on PostgreSQL (IP-01, IP-04).

Jira: progress runs under the same guard as release and never changes
``ProductionSchedule.version``. IP-01 checks the order's tasks in the effective
``released_version`` after progress is carried forward; the final task and the
unique deduction commit together, and a replay lowers stock no further. Fig. 10:
1,000 g flour -> 400 g (T-IP01-ONCE, T-C1-REPLAN). While a revised plan waits
for review, only the tasks it keeps unchanged can progress.
"""

import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth.access import Identity, require_owner
from app.config import Settings
from app.inventory_production import (
    InventoryService,
    PlanStatus,
    ProductionPlanner,
    ProgressRefused,
    ReleaseStatus,
    ScheduleRelease,
    TaskProgress,
)
from app.inventory_production.fixtures import proposal_reply
from app.inventory_production.routes import make_router
from app.llm.gateway import MockLLMClient
from app.shared import RecordNotFound, RecordValidationError
from app.shared.records import (
    AuditEntry,
    InventoryItem,
    Order,
    OrderDeduction,
    OrderItem,
    OrderStatus,
    ProductionSchedule,
    ProductionTask,
    TaskStatus,
)

SYDNEY = ZoneInfo("Australia/Sydney")
DAY = date(2026, 11, 5)
STARTED, DONE = TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED


def at(hour, minute=0):
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=SYDNEY)


def order(store, seeded, cupcakes=24):
    """A confirmed vanilla cupcake order: 24 need 600 g flour and 90 minutes of work."""
    with store.unit_of_work() as session:
        record = Order(
            customer_id=seeded.customer_id,
            required_at=at(12).astimezone(UTC),
            status=OrderStatus.CONFIRMED,
            collection_kind="before",
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=cupcakes)],
        )
        return store.commit(session, record, actor_id=seeded.owner_id, action="test.order")


def figure10(ref, finish=None):
    """A 60-minute bake from 06:00 and then a 30-minute finish, from 07:00 unless given."""
    return [
        {"order_id": str(ref.record_id), "task_kind": kind, "start_at": begin.isoformat(),
         "end_at": (begin + timedelta(minutes=minutes)).isoformat()}
        for kind, begin, minutes in (("bake", at(6), 60), ("finish", finish or at(7), 30))
    ]


def plan(store, seeded, tasks, schedule_id=None):
    outcome = ProductionPlanner(store, MockLLMClient(default_reply=proposal_reply(tasks))).propose(
        DAY, DAY, actor_id=seeded.owner_id, schedule_id=schedule_id
    )
    assert outcome.status is PlanStatus.PROPOSED, outcome.problems
    return outcome.schedule_ref


def release(store, seeded, ref):
    outcome = ScheduleRelease(store).approve(ref.record_id, ref.version, owner_id=seeded.owner_id)
    assert outcome.status is ReleaseStatus.RELEASED, outcome.problems


def released_plan(store, seeded, cupcakes):
    ref = plan(store, seeded, figure10(cupcakes))
    release(store, seeded, ref)
    return ref


def task(store, ref, kind, version=None):
    """The id of the ``kind`` task in ``version`` of the plan (its latest by default)."""
    with store.unit_of_work() as session:
        version = version or store.load(session, ProductionSchedule, ref.record_id).version
        return session.scalars(
            select(ProductionTask.task_id).where(
                ProductionTask.schedule_id == ref.record_id,
                ProductionTask.schedule_version == version,
                ProductionTask.task_kind == kind,
            )
        ).one()


def record(store, seeded, ref, task_id, status):
    return TaskProgress(store).record(ref.record_id, task_id, status, actor_id=seeded.owner_id)


def status_of(store, model, record_id):
    with store.unit_of_work() as session:
        return session.get(model, record_id).status


def on_hand(store, seeded, name="Flour"):
    with store.unit_of_work() as session:
        return session.get(InventoryItem, seeded.items[name]).quantity_on_hand


def count(store, model):
    with store.unit_of_work() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_figure10_deducts_once_through_a_revision(store, seeded):
    cupcakes = order(store, seeded)
    first = released_plan(store, seeded, cupcakes)

    started = record(store, seeded, first, task(store, first, "bake"), STARTED)
    baked = record(store, seeded, first, task(store, first, "bake"), DONE)

    # Completing the bake does not deduct; progress leaves the version alone.
    assert (started.order_status, started.deduction) == (OrderStatus.IN_PRODUCTION, None)
    assert (baked.task_status, baked.order_status, baked.deduction) == (DONE, OrderStatus.IN_PRODUCTION, None)
    assert on_hand(store, seeded) == Decimal("1000.000")
    with store.unit_of_work() as session:
        schedule = store.load(session, ProductionSchedule, first.record_id)
        assert (schedule.version, schedule.released_version) == (1, 1)

    # The revision keeps the bake and moves the finish; release carries the bake's status.
    revised = plan(store, seeded, figure10(cupcakes, finish=at(7, 30)), schedule_id=first.record_id)
    release(store, seeded, revised)
    assert status_of(store, ProductionTask, task(store, revised, "bake")) == DONE

    finished = record(store, seeded, revised, task(store, revised, "finish"), DONE)

    assert (finished.task_status, finished.order_status, finished.replayed) == (DONE, OrderStatus.COMPLETED, False)
    deduction = finished.deduction
    assert deduction.schedule_version == 2
    assert [(line.ingredient, line.quantity, line.on_hand) for line in deduction.lines] == [
        ("Butter", Decimal("300.000"), Decimal("1200.000")),
        ("Caster sugar", Decimal("400.000"), Decimal("1600.000")),
        ("Eggs", Decimal("6.000"), Decimal("24.000")),
        ("Flour", Decimal("600.000"), Decimal("400.000")),
        ("Icing sugar", Decimal("500.000"), Decimal("1000.000")),
        ("Milk", Decimal("240.000"), Decimal("1760.000")),
        ("Vanilla extract", Decimal("20.000"), Decimal("80.000")),
    ]
    flour = next(level for level in InventoryService(store).stock_levels() if level.ingredient == "Flour")
    # Deducted and no longer committed: counted once, not twice.
    assert (flour.on_hand, flour.committed, flour.available) == (Decimal("400.000"), Decimal("0.000"), Decimal("400.000"))

    again = record(store, seeded, revised, task(store, revised, "finish"), DONE)

    assert again.replayed
    assert again.deduction.deduction_id == deduction.deduction_id
    assert on_hand(store, seeded) == Decimal("400.000")
    assert count(store, OrderDeduction) == 1
    with store.unit_of_work() as session:
        audit = session.scalars(select(AuditEntry).where(AuditEntry.action == "order.deducted")).one()
        assert audit.subject_id == deduction.deduction_id
        assert len(audit.after_data["lines"]) == 7


def test_old_and_unreleased_versions_take_no_progress(store, seeded):
    cupcakes = order(store, seeded)
    first = released_plan(store, seeded, cupcakes)
    revised = plan(store, seeded, figure10(cupcakes, finish=at(7, 30)), schedule_id=first.record_id)

    with pytest.raises(ProgressRefused, match="Version 2 of this plan is not released yet"):
        record(store, seeded, revised, task(store, revised, "bake"), STARTED)
    release(store, seeded, revised)
    with pytest.raises(ProgressRefused, match="Version 1 of this plan was replaced by version 2"):
        record(store, seeded, first, task(store, first, "finish", version=1), DONE)

    assert status_of(store, Order, cupcakes.record_id) == OrderStatus.SCHEDULED


def test_pending_revision_pauses_only_the_tasks_it_changes(store, seeded):
    cupcakes = order(store, seeded)
    first = released_plan(store, seeded, cupcakes)
    # Version 2 keeps the bake and moves the finish to 07:30.
    revised = plan(store, seeded, figure10(cupcakes, finish=at(7, 30)), schedule_id=first.record_id)
    bake, finish = task(store, first, "bake", version=1), task(store, first, "finish", version=1)

    assert record(store, seeded, first, bake, STARTED).task_status == STARTED
    with pytest.raises(ProgressRefused) as refused:
        record(store, seeded, first, finish, STARTED)

    assert str(refused.value) == (
        f"The finish task for order {str(cupcakes.record_id)[:8]} (Thu 07:00–07:30) is paused: version 2 of"
        " this plan moves or removes it and is not released yet. Approve version 2 or request a revised"
        " plan that keeps the task."
    )
    assert status_of(store, ProductionTask, finish) == TaskStatus.PLANNED
    # Released, the moved finish can start; the bake's progress came with it.
    release(store, seeded, revised)
    assert record(store, seeded, revised, task(store, revised, "finish"), STARTED).task_status == STARTED
    assert status_of(store, ProductionTask, task(store, revised, "bake")) == STARTED


def test_concurrent_completions_deduct_once(store, seeded):
    cupcakes = order(store, seeded)
    ref = released_plan(store, seeded, cupcakes)
    record(store, seeded, ref, task(store, ref, "bake"), DONE)
    finish = task(store, ref, "finish")
    barrier = threading.Barrier(2)
    outcomes = []

    def complete():
        barrier.wait()
        outcomes.append(record(store, seeded, ref, finish, DONE))

    threads = [threading.Thread(target=complete) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert sorted(o.replayed for o in outcomes) == [False, True]
    assert len({o.deduction.deduction_id for o in outcomes}) == 1
    assert on_hand(store, seeded) == Decimal("400.000")
    assert count(store, OrderDeduction) == 1


def test_failed_deduction_completes_nothing(store, seeded, monkeypatch):
    cupcakes = order(store, seeded)
    ref = released_plan(store, seeded, cupcakes)
    record(store, seeded, ref, task(store, ref, "bake"), DONE)
    audited = count(store, AuditEntry)
    commit = store.commit

    def failing(session, record, **kwargs):
        if kwargs.get("action") == "stock.deducted":
            raise RecordValidationError("simulated failed save")
        return commit(session, record, **kwargs)

    monkeypatch.setattr(store, "commit", failing)
    with pytest.raises(RecordValidationError):
        record(store, seeded, ref, task(store, ref, "finish"), DONE)

    # Never reported or saved as complete without its deduction.
    assert status_of(store, ProductionTask, task(store, ref, "finish")) == TaskStatus.PLANNED
    assert status_of(store, Order, cupcakes.record_id) == OrderStatus.IN_PRODUCTION
    assert on_hand(store, seeded) == Decimal("1000.000")
    assert (count(store, OrderDeduction), count(store, AuditEntry)) == (0, audited)


def test_stock_that_no_longer_covers_the_order_refuses_completion(store, seeded):
    cupcakes = order(store, seeded)
    ref = released_plan(store, seeded, cupcakes)
    record(store, seeded, ref, task(store, ref, "bake"), DONE)
    with store.constraint_guard() as session:  # a stock count found less flour
        flour = store.load(session, InventoryItem, seeded.items["Flour"], lock=True)
        flour.quantity_on_hand = Decimal("500.000")
        store.commit(session, flour, actor_id=seeded.owner_id, action="test.count",
                     expected_version=flour.version, revise=True)

    with pytest.raises(ProgressRefused, match="short of 100 g flour. Nothing was recorded"):
        record(store, seeded, ref, task(store, ref, "finish"), DONE)

    assert status_of(store, ProductionTask, task(store, ref, "finish")) == TaskStatus.PLANNED
    assert status_of(store, Order, cupcakes.record_id) == OrderStatus.IN_PRODUCTION
    assert (on_hand(store, seeded), count(store, OrderDeduction)) == (Decimal("500.000"), 0)


def test_cancelled_or_changed_orders_take_no_progress(store, seeded):
    cancelled, changed = order(store, seeded, cupcakes=12), order(store, seeded, cupcakes=12)
    ref = plan(store, seeded, figure10(cancelled) + [
        {**t, "start_at": (datetime.fromisoformat(t["start_at"]) + timedelta(minutes=90)).isoformat(),
         "end_at": (datetime.fromisoformat(t["end_at"]) + timedelta(minutes=90)).isoformat()}
        for t in figure10(changed)
    ])
    release(store, seeded, ref)
    with store.constraint_guard() as session:  # stand-in for BAKE-30's cancellation
        held = store.load(session, Order, cancelled.record_id)
        held.status = OrderStatus.CANCELLED
        store.commit(session, held, actor_id=seeded.owner_id, action="test.cancel",
                     expected_version=held.version)
    with store.unit_of_work() as session:
        held = store.load(session, Order, changed.record_id)
        held.items[0].quantity = 24
        store.commit(session, held, actor_id=seeded.owner_id, action="test.edit",
                     expected_version=held.version, revise=True)

    with store.unit_of_work() as session:
        tasks = {
            t.order_id: t.task_id
            for t in session.scalars(select(ProductionTask).where(ProductionTask.task_kind == "bake"))
        }
    with pytest.raises(ProgressRefused, match=f"Order {str(cancelled.record_id)[:8]} is cancelled"):
        record(store, seeded, ref, tasks[cancelled.record_id], STARTED)
    with pytest.raises(ProgressRefused, match="has changed since this plan was released"):
        record(store, seeded, ref, tasks[changed.record_id], STARTED)


def test_unknown_task_or_wrong_plan_is_not_found(store, seeded):
    cupcakes = order(store, seeded)
    ref = released_plan(store, seeded, cupcakes)

    with pytest.raises(RecordNotFound):
        TaskProgress(store).record(ref.record_id, uuid.uuid4(), STARTED, actor_id=seeded.owner_id)
    with pytest.raises(RecordNotFound):
        TaskProgress(store).record(uuid.uuid4(), task(store, ref, "bake"), STARTED, actor_id=seeded.owner_id)


def test_completed_task_cannot_start_again(store, seeded):
    cupcakes = order(store, seeded)
    ref = released_plan(store, seeded, cupcakes)
    bake = task(store, ref, "bake")
    record(store, seeded, ref, bake, DONE)

    with pytest.raises(ProgressRefused, match="This task is already completed"):
        record(store, seeded, ref, bake, STARTED)


@pytest.fixture
def api(store, seeded) -> TestClient:
    app = FastAPI()
    app.include_router(make_router(Settings(_env_file=None), store=store))
    app.dependency_overrides[require_owner] = lambda: Identity(seeded.owner_id, "Owner", "Owner", None)
    return TestClient(app)


def test_owner_records_progress_through_the_api(api, store, seeded):
    cupcakes = order(store, seeded)
    ref = released_plan(store, seeded, cupcakes)
    url = f"/api/schedules/{ref.record_id}/tasks/{{}}/progress"

    baked = api.post(url.format(task(store, ref, "bake")), json={"status": "Completed"})
    assert baked.status_code == 200, baked.json()
    assert (baked.json()["orderStatus"], baked.json()["deduction"]) == ("InProduction", None)

    finished = api.post(url.format(task(store, ref, "finish")), json={"status": "Completed"}).json()
    assert (finished["status"], finished["orderStatus"], finished["replayed"]) == ("Completed", "Completed", False)
    assert finished["order"]["recordId"] == str(cupcakes.record_id)
    flour = next(line for line in finished["deduction"]["lines"] if line["ingredient"] == "Flour")
    assert (flour["quantity"], flour["onHand"], flour["unit"]) == ("600.000", "400.000", "g")

    again = api.post(url.format(task(store, ref, "finish")), json={"status": "Completed"}).json()
    assert again["replayed"] and again["deduction"]["id"] == finished["deduction"]["id"]

    refused = api.post(url.format(task(store, ref, "bake")), json={"status": "InProgress"})
    assert refused.status_code == 409
    assert refused.json()["detail"] == "This task is already completed."
    assert api.post(url.format(uuid.uuid4()), json={"status": "Completed"}).status_code == 404
