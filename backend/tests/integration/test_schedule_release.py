"""BAKE-21: UC-04 steps 6-9 on PostgreSQL: approval and the guarded release.

Jira: a valid draft and its approval request are saved atomically; the plan is
Approved only after the final guarded checks and the committed release; at
release, unchanged tasks take the latest status of the previous effective
version by ``logical_task_id``, including progress made after the draft;
unsafe changes to started or completed work are refused; competing releases
cannot both use the same stock (T-IP02-RACE, T-APPROVAL-STALE, T-C1-REPLAN).

Task progress is BAKE-33's ``TaskProgress``. Order cancellation is BAKE-30;
``cancel`` below stands in for its guarded write.
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
    ProgressRefused,
    ProductionPlanner,
    ReleaseStatus,
    ScheduleRelease,
    ScheduleState,
    TaskProgress,
)
from app.inventory_production.fixtures import GreedyMockPlanner, proposal_reply
from app.inventory_production.routes import make_router
from app.llm.gateway import MockLLMClient
from app.shared import AlreadyDecided, RecordValidationError, StaleVersionError
from app.shared.records import (
    ApprovalDecision,
    ApprovalRequest,
    AuditEntry,
    BakerySettings,
    InventoryItem,
    Order,
    OrderItem,
    OrderStatus,
    ProductionSchedule,
    ProductionTask,
    TaskStatus,
)

SYDNEY = ZoneInfo("Australia/Sydney")
DAY = date(2026, 11, 5)


def at(hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SYDNEY)


def order(store, seeded, cupcakes=24, due=None):
    """A confirmed vanilla cupcake order: 24 need 600 g flour and 90 minutes of work."""
    with store.unit_of_work() as session:
        record = Order(
            customer_id=seeded.customer_id,
            required_at=(due or at(12)).astimezone(UTC),
            status=OrderStatus.CONFIRMED,
            collection_kind="before",
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=cupcakes)],
        )
        return store.commit(session, record, actor_id=seeded.owner_id, action="test.order")


def figure10(ref, start=None, finish=None):
    """A 60-minute bake and then a 30-minute finish, from 06:00 unless given."""
    start = start or at(6)
    finish = finish or start + timedelta(minutes=60)
    return [
        {"order_id": str(ref.record_id), "task_kind": kind, "start_at": begin.isoformat(),
         "end_at": (begin + timedelta(minutes=minutes)).isoformat()}
        for kind, begin, minutes in (("bake", start, 60), ("finish", finish, 30))
    ]


def plan(store, seeded, tasks=None, *, day=DAY, schedule_id=None):
    """A saved draft version: the Mock planner's, or one with exactly ``tasks``."""
    client = GreedyMockPlanner() if tasks is None else MockLLMClient(default_reply=proposal_reply(tasks))
    outcome = ProductionPlanner(store, client).propose(
        day, day, actor_id=seeded.owner_id, schedule_id=schedule_id
    )
    assert outcome.status is PlanStatus.PROPOSED, outcome.problems
    return outcome.schedule_ref


def approve(store, seeded, ref):
    return ScheduleRelease(store).approve(ref.record_id, ref.version, owner_id=seeded.owner_id)


def released_tasks(store, schedule_ref, **where):
    """Ids of the released version's tasks matching ``where``, in time order."""
    with store.unit_of_work() as session:
        schedule = store.load(session, ProductionSchedule, schedule_ref.record_id)
        return session.scalars(
            select(ProductionTask.task_id)
            .filter_by(schedule_id=schedule.schedule_id, schedule_version=schedule.released_version, **where)
            .order_by(ProductionTask.start_at)
        ).all()


def progress(store, seeded, schedule_ref, kind, status):
    """BAKE-33's guarded progress on the released version's ``kind`` task."""
    [task_id] = released_tasks(store, schedule_ref, task_kind=kind)
    TaskProgress(store).record(schedule_ref.record_id, task_id, status, actor_id=seeded.owner_id)


def cancel(store, seeded, ref):
    """Stand-in for BAKE-30's guarded cancellation."""
    with store.constraint_guard() as session:
        record = store.load(session, Order, ref.record_id)
        record.status = OrderStatus.CANCELLED
        store.commit(session, record, actor_id=seeded.owner_id, action="test.cancel",
                     expected_version=record.version)


def change_quantity(store, seeded, ref, cupcakes):
    with store.unit_of_work() as session:
        record = store.load(session, Order, ref.record_id)
        record.items[0].quantity = cupcakes
        store.commit(session, record, actor_id=seeded.owner_id, action="test.edit",
                     expected_version=record.version, revise=True)


def request_for(session, ref):
    return session.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.subject_id == ref.record_id, ApprovalRequest.subject_version == ref.version
        )
    ).one()


def status_of(store, ref):
    with store.unit_of_work() as session:
        return store.load(session, Order, ref.record_id).status


def flour_committed(store, seeded):
    with store.unit_of_work() as session:
        return InventoryService(store).committed_needs(session).get(seeded.items["Flour"], Decimal(0))


def tasks_of(store, schedule_ref, version):
    with store.unit_of_work() as session:
        return {
            t.task_kind: t.status
            for t in session.scalars(
                select(ProductionTask).where(
                    ProductionTask.schedule_id == schedule_ref.record_id,
                    ProductionTask.schedule_version == version,
                )
            )
        }


def view(store, schedule_ref):
    _, views = ScheduleRelease(store).schedules()
    return next(v for v in views if v.schedule_ref.record_id == schedule_ref.record_id)


def test_draft_is_saved_with_its_approval_request(store, seeded):
    order(store, seeded)

    ref = plan(store, seeded)

    with store.unit_of_work() as session:
        request = request_for(session, ref)
        assert (request.kind, request.subject_type, request.subject_version) == ("Schedule", "ProductionSchedule", 1)
        assert request.decision is None
        assert store.load(session, ProductionSchedule, ref.record_id).released_version is None
    assert view(store, ref).state is ScheduleState.AWAITING_APPROVAL


def test_approval_releases_the_plan_and_commits_its_stock_once(store, seeded):
    cupcakes = order(store, seeded)
    ref = plan(store, seeded)

    outcome = approve(store, seeded, ref)

    assert outcome.status is ReleaseStatus.RELEASED, outcome.problems
    with store.unit_of_work() as session:
        assert store.load(session, ProductionSchedule, ref.record_id).released_version == 1
        request = request_for(session, ref)
        assert (request.decision, request.invalidated_at) == (ApprovalDecision.APPROVED, None)
        # Fig. 10: 600 g is committed, but stock stays 1,000 g until IP-01 deducts it.
        assert session.get(InventoryItem, seeded.items["Flour"]).quantity_on_hand == Decimal("1000.000")
        actions = set(session.scalars(select(AuditEntry.action)))
    assert {"approval.request", "approval.approved", "schedule.released", "order.schedule_released"} <= actions
    assert status_of(store, cupcakes) == OrderStatus.SCHEDULED
    assert flour_committed(store, seeded) == Decimal("600.000")
    assert view(store, ref).state is ScheduleState.APPROVED


def two_plans_for_one_flour(store, seeded):
    """Two drafts on different days, each needing 600 g of the 1,000 g flour."""
    first_order = order(store, seeded)
    first = plan(store, seeded)
    second_order = order(store, seeded, due=at(12, day=DAY + timedelta(days=1)))
    second = plan(store, seeded, day=DAY + timedelta(days=1))
    return (first_order, first), (second_order, second)


def test_second_plan_for_the_same_flour_is_blocked_and_its_approval_invalidated(store, seeded):
    (_, first), (second_order, second) = two_plans_for_one_flour(store, seeded)
    assert approve(store, seeded, first).status is ReleaseStatus.RELEASED

    outcome = approve(store, seeded, second)

    assert outcome.status is ReleaseStatus.BLOCKED
    assert outcome.problems == ("Stock no longer covers the plan: short of 200 g flour.",)
    with store.unit_of_work() as session:
        assert store.load(session, ProductionSchedule, second.record_id).released_version is None
        request = request_for(session, second)
        # The decision stays as history but can never release (UC-04 8a).
        assert request.decision == ApprovalDecision.APPROVED
        assert request.invalidated_at is not None
        assert "short of 200 g flour" in request.invalidation_reason
    assert status_of(store, second_order) == OrderStatus.CONFIRMED
    assert flour_committed(store, seeded) == Decimal("600.000")
    shown = view(store, second)
    assert shown.state is ScheduleState.NEEDS_REVISION
    assert "could not be released" in shown.notice
    with pytest.raises(AlreadyDecided):
        approve(store, seeded, second)


def test_concurrent_releases_cannot_both_use_the_same_flour(store, seeded):
    (_, first), (_, second) = two_plans_for_one_flour(store, seeded)
    barrier, results, errors = threading.Barrier(2), [], []

    def release(ref):
        barrier.wait()
        try:
            results.append(approve(store, seeded, ref).status)
        except Exception as exc:  # reported below, not lost in the thread
            errors.append(exc)

    threads = [threading.Thread(target=release, args=(ref,)) for ref in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert sorted(results) == sorted([ReleaseStatus.RELEASED, ReleaseStatus.BLOCKED])
    assert flour_committed(store, seeded) == Decimal("600.000")


def test_order_released_in_another_plan_blocks_this_one(store, seeded):
    order(store, seeded)
    first, second = plan(store, seeded), plan(store, seeded)  # both drafts hold the order
    assert approve(store, seeded, first).status is ReleaseStatus.RELEASED

    outcome = approve(store, seeded, second)

    assert outcome.status is ReleaseStatus.BLOCKED
    assert "is now in another released plan" in outcome.problems[0]


def test_order_cancelled_after_the_draft_blocks_the_release(store, seeded):
    cupcakes = order(store, seeded)
    ref = plan(store, seeded)
    cancel(store, seeded, cupcakes)

    outcome = approve(store, seeded, ref)

    assert outcome.status is ReleaseStatus.BLOCKED
    assert outcome.problems == (f"Order {str(cupcakes.record_id)[:8]} is now Cancelled.",)
    assert flour_committed(store, seeded) == 0


def test_order_changed_after_the_draft_blocks_the_release(store, seeded):
    cupcakes = order(store, seeded)
    ref = plan(store, seeded)
    change_quantity(store, seeded, cupcakes, 12)

    outcome = approve(store, seeded, ref)

    assert outcome.status is ReleaseStatus.BLOCKED
    assert "has changed since this plan was made" in outcome.problems[0]


def test_capacity_lowered_after_the_draft_blocks_the_release(store, seeded):
    order(store, seeded)
    ref = plan(store, seeded)
    with store.constraint_guard() as session:
        settings = session.scalars(select(BakerySettings)).one()
        settings.default_daily_capacity_minutes = 60
        store.commit(session, settings, actor_id=seeded.owner_id, action="test.capacity",
                     expected_version=settings.version, revise=True)

    outcome = approve(store, seeded, ref)

    assert outcome.status is ReleaseStatus.BLOCKED
    assert any("has 90 minutes of work; the team has 60." in p for p in outcome.problems), outcome.problems


def test_old_approval_cannot_release_a_revised_plan(store, seeded):
    cupcakes = order(store, seeded)
    first = plan(store, seeded, figure10(cupcakes))
    revised = plan(store, seeded, figure10(cupcakes, at(7)), schedule_id=first.record_id)
    assert revised.version == 2

    with pytest.raises(StaleVersionError):
        approve(store, seeded, first)

    with store.unit_of_work() as session:
        assert request_for(session, first).decision is None  # nothing was recorded
        assert store.load(session, ProductionSchedule, first.record_id).released_version is None
    assert approve(store, seeded, revised).status is ReleaseStatus.RELEASED


def test_release_copies_progress_made_after_the_draft(store, seeded):
    cupcakes = order(store, seeded)
    first = plan(store, seeded, figure10(cupcakes))
    approve(store, seeded, first)
    # The revision keeps the bake and moves the finish.
    revised = plan(store, seeded, figure10(cupcakes, finish=at(7, 30)), schedule_id=first.record_id)
    progress(store, seeded, first, "bake", TaskStatus.COMPLETED)  # after the draft was made
    pending = view(store, revised)
    assert (pending.state, pending.released_version) == (ScheduleState.AWAITING_APPROVAL, 1)
    assert [t.status for t in pending.released.tasks] == [TaskStatus.COMPLETED, TaskStatus.PLANNED]

    outcome = approve(store, seeded, revised)

    assert outcome.status is ReleaseStatus.RELEASED, outcome.problems
    assert tasks_of(store, revised, 2) == {"bake": TaskStatus.COMPLETED, "finish": TaskStatus.PLANNED}
    assert tasks_of(store, first, 1)["bake"] == TaskStatus.COMPLETED
    # The revision replaces the released commitment rather than adding to it.
    assert flour_committed(store, seeded) == Decimal("600.000")
    # The finished bake put the order in production, and the release keeps it there.
    assert status_of(store, cupcakes) == OrderStatus.IN_PRODUCTION


@pytest.mark.parametrize(("status", "words"), [(TaskStatus.IN_PROGRESS, "in progress"), (TaskStatus.COMPLETED, "completed")])
def test_plan_that_moves_started_work_is_refused(store, seeded, status, words):
    cupcakes = order(store, seeded)
    first = plan(store, seeded, figure10(cupcakes))
    approve(store, seeded, first)
    progress(store, seeded, first, "bake", status)  # before the revision, which then pauses it
    revised = plan(store, seeded, figure10(cupcakes, at(8)), schedule_id=first.record_id)

    outcome = approve(store, seeded, revised)

    assert outcome.status is ReleaseStatus.BLOCKED
    assert outcome.problems == (
        f"The bake task for order {str(cupcakes.record_id)[:8]} (Thu 06:00–07:00) is {words},"
        " and this plan moves or removes it. Keep it unchanged in a revised plan.",
    )
    with store.unit_of_work() as session:
        assert store.load(session, ProductionSchedule, first.record_id).released_version == 1
    assert tasks_of(store, first, 1)["bake"] == status


def test_revision_that_leaves_out_an_order_makes_it_confirmed_again(store, seeded):
    early = order(store, seeded, cupcakes=12, due=at(10))
    later = order(store, seeded, cupcakes=12, due=at(12))
    first = plan(store, seeded)
    approve(store, seeded, first)
    change_quantity(store, seeded, later, 48)  # 1,200 g: the revision leaves it out
    revised = plan(store, seeded, schedule_id=first.record_id)

    outcome = approve(store, seeded, revised)

    assert outcome.status is ReleaseStatus.RELEASED, outcome.problems
    assert (status_of(store, early), status_of(store, later)) == (OrderStatus.SCHEDULED, OrderStatus.CONFIRMED)
    assert flour_committed(store, seeded) == Decimal("300.000")


def test_withheld_plan_stays_pending_and_can_be_approved_later(store, seeded):
    cupcakes = order(store, seeded)
    ref = plan(store, seeded)

    outcome = ScheduleRelease(store).withhold(ref.record_id, ref.version, owner_id=seeded.owner_id)

    # UC-04 7b / Fig. 22: "withhold / remain pending"; a later decision resumes.
    assert outcome.status is ReleaseStatus.WITHHELD
    with store.unit_of_work() as session:
        request = request_for(session, ref)
        assert request.decision is None
        audit = session.scalars(
            select(AuditEntry).where(
                AuditEntry.subject_id == request.request_id, AuditEntry.action == "approval.withheld"
            )
        ).one()
        assert audit.actor_id == seeded.owner_id
        assert store.load(session, ProductionSchedule, ref.record_id).released_version is None
    assert status_of(store, cupcakes) == OrderStatus.CONFIRMED
    shown = view(store, ref)
    assert shown.state is ScheduleState.AWAITING_APPROVAL
    assert shown.notice.startswith("You haven't approved version 1 yet. It stays unreleased")
    assert approve(store, seeded, ref).status is ReleaseStatus.RELEASED


def test_withholding_a_decided_or_old_version_is_refused(store, seeded):
    cupcakes = order(store, seeded)
    first = plan(store, seeded, figure10(cupcakes))
    revised = plan(store, seeded, figure10(cupcakes, at(7)), schedule_id=first.record_id)
    release = ScheduleRelease(store)

    with pytest.raises(StaleVersionError):
        release.withhold(first.record_id, first.version, owner_id=seeded.owner_id)
    approve(store, seeded, revised)
    with pytest.raises(AlreadyDecided):
        release.withhold(revised.record_id, revised.version, owner_id=seeded.owner_id)


def complete(store, seeded, schedule_ref, order_ref):
    """BAKE-33: finish the order's released tasks; the last one deducts its stock once (TX-DEDUCT)."""
    progress = TaskProgress(store)
    for task_id in released_tasks(store, schedule_ref, order_id=order_ref.record_id):
        outcome = progress.record(schedule_ref.record_id, task_id, TaskStatus.COMPLETED, actor_id=seeded.owner_id)
    assert outcome.order_status == OrderStatus.COMPLETED


@pytest.mark.parametrize("when", ["before the revision", "while it awaits approval"])
def test_order_completed_meanwhile_does_not_block_the_rest_of_the_plan(store, seeded, when):
    done, rest = order(store, seeded, cupcakes=12, due=at(10)), order(store, seeded, cupcakes=12, due=at(12))
    first = plan(store, seeded)
    approve(store, seeded, first)
    if when == "before the revision":
        complete(store, seeded, first, done)  # the revision leaves it out
    revised = plan(store, seeded, schedule_id=first.record_id)
    if when == "while it awaits approval":
        complete(store, seeded, first, done)  # the revision still holds its tasks

    outcome = approve(store, seeded, revised)

    assert outcome.status is ReleaseStatus.RELEASED, outcome.problems
    assert (status_of(store, done), status_of(store, rest)) == (OrderStatus.COMPLETED, OrderStatus.SCHEDULED)
    # Only the remaining order is committed; the completed one was deducted once.
    assert flour_committed(store, seeded) == Decimal("300.000")
    with store.unit_of_work() as session:
        assert session.get(InventoryItem, seeded.items["Flour"]).quantity_on_hand == Decimal("700.000")
        statuses = {
            t.order_id: t.status
            for t in session.scalars(select(ProductionTask).where(ProductionTask.schedule_version == 2))
        }
    if when == "while it awaits approval":
        assert statuses[done.record_id] == TaskStatus.COMPLETED
    else:
        assert done.record_id not in statuses


def test_order_whose_tasks_the_pending_revision_moves_waits_for_it(store, seeded):
    moved = order(store, seeded, cupcakes=12)
    first = plan(store, seeded, figure10(moved))
    approve(store, seeded, first)
    revised = plan(store, seeded, figure10(moved, at(8)), schedule_id=first.record_id)

    # Paused rather than completed under a plan that moves it, so the release is safe.
    with pytest.raises(ProgressRefused, match="is paused: version 2 of this plan moves or removes it"):
        complete(store, seeded, first, moved)
    assert approve(store, seeded, revised).status is ReleaseStatus.RELEASED

    complete(store, seeded, revised, moved)
    with store.unit_of_work() as session:
        assert session.get(InventoryItem, seeded.items["Flour"]).quantity_on_hand == Decimal("700.000")


def test_failed_save_rolls_back_the_decision_and_the_release(store, seeded, monkeypatch):
    cupcakes = order(store, seeded)
    ref = plan(store, seeded)
    with store.unit_of_work() as session:
        audited = session.scalar(select(func.count()).select_from(AuditEntry))
    commit = store.commit

    def failing(session, record, **kwargs):
        if kwargs.get("action") == "order.schedule_released":
            raise RecordValidationError("simulated failed save")
        return commit(session, record, **kwargs)

    monkeypatch.setattr(store, "commit", failing)
    with pytest.raises(RecordValidationError):
        approve(store, seeded, ref)

    # UC-04 8b: nothing half-done, and no decision left looking usable.
    with store.unit_of_work() as session:
        assert request_for(session, ref).decision is None
        assert store.load(session, ProductionSchedule, ref.record_id).released_version is None
        assert session.scalar(select(func.count()).select_from(AuditEntry)) == audited
    assert status_of(store, cupcakes) == OrderStatus.CONFIRMED


def test_cancelled_order_frees_its_time_for_another_plan(store, seeded):
    cancelled = order(store, seeded, cupcakes=12)
    approve(store, seeded, plan(store, seeded))  # its tasks run from 06:00
    cancel(store, seeded, cancelled)
    order(store, seeded)

    outcome = ProductionPlanner(store, GreedyMockPlanner()).propose(DAY, DAY, actor_id=seeded.owner_id)

    assert outcome.status is PlanStatus.PROPOSED, outcome.problems
    assert outcome.tasks[0].start_at.astimezone(SYDNEY) == at(6)


@pytest.mark.parametrize("plan_for", ["a revision of the same plan", "another plan"])
def test_completed_order_left_out_of_a_revision_still_uses_its_time(store, seeded, plan_for):
    # Three 90-minute orders of 300 g flour each: stock covers them all, 240 minutes do not.
    done, rest = order(store, seeded, cupcakes=12, due=at(10)), order(store, seeded, cupcakes=12, due=at(12))
    first = plan(store, seeded, figure10(done, at(6)) + figure10(rest, at(7, 30)))
    approve(store, seeded, first)
    complete(store, seeded, first, done)  # 06:00-07:30 was used
    if plan_for == "another plan":
        revised = plan(store, seeded, figure10(rest, at(7, 30)), schedule_id=first.record_id)
        assert approve(store, seeded, revised).status is ReleaseStatus.RELEASED
    extra = order(store, seeded, cupcakes=12, due=at(12))
    tasks, schedule_id = figure10(extra, at(9)), None
    if plan_for == "a revision of the same plan":
        tasks, schedule_id = figure10(rest, at(7, 30)) + tasks, first.record_id

    client = MockLLMClient(default_reply=proposal_reply(tasks))
    outcome = ProductionPlanner(store, client).propose(
        DAY, DAY, actor_id=seeded.owner_id, schedule_id=schedule_id
    )

    assert outcome.status is PlanStatus.NEEDS_REVISION
    assert "Thu 05 Nov has 270 minutes of work; the team has 240." in outcome.problems


def test_plan_list_reads_one_snapshot_while_a_release_commits(store, seeded):
    order(store, seeded)
    ref = plan(store, seeded)
    released = []

    class ReleasedMidway(ScheduleRelease):
        def _view(self, session, schedule):
            # Commits after the list read the plan and before it reads the request.
            writer = threading.Thread(target=lambda: released.append(approve(store, seeded, ref)))
            writer.start()
            writer.join(timeout=10)
            return super()._view(session, schedule)

    _, [shown] = ReleasedMidway(store).schedules()

    assert [o.status for o in released] == [ReleaseStatus.RELEASED]
    # As before the release, not "could not be released".
    assert (shown.state, shown.notice) == (ScheduleState.AWAITING_APPROVAL, "")
    assert view(store, ref).state is ScheduleState.APPROVED


@pytest.fixture
def api(store, seeded) -> TestClient:
    app = FastAPI()
    app.include_router(make_router(Settings(_env_file=None), store=store, plan_client_factory=GreedyMockPlanner))
    app.dependency_overrides[require_owner] = lambda: Identity(seeded.owner_id, "Owner", "Owner", None)
    return TestClient(app)


def test_owner_plans_reviews_and_releases_through_the_api(api, store, seeded):
    cupcakes = order(store, seeded)

    proposed = api.post("/api/schedules", json={"startDate": "2026-11-05", "endDate": "2026-11-05"})
    assert proposed.status_code == 201, proposed.json()
    schedule = proposed.json()["schedule"]
    listed = api.get("/api/schedules").json()
    assert listed["timeZone"] == "Australia/Sydney"
    shown = listed["schedules"][0]
    assert (shown["id"], shown["state"], shown["releasedVersion"]) == (schedule["recordId"], "AwaitingApproval", None)
    assert [(t["kind"], t["startAt"], t["status"]) for t in shown["latest"]["tasks"]] == [
        ("bake", "2026-11-05T06:00:00+11:00", "Planned"), ("finish", "2026-11-05T07:00:00+11:00", "Planned"),
    ]

    url = f"/api/schedules/{schedule['recordId']}/decision"
    released = api.post(url, json={"version": 1, "decision": "Approved"})
    assert released.json() == {"status": "Released", "schedule": schedule, "problems": []}
    assert api.get("/api/schedules").json()["schedules"][0]["state"] == "Approved"
    assert status_of(store, cupcakes) == OrderStatus.SCHEDULED

    again = api.post(url, json={"version": 1, "decision": "Approved"})
    assert again.status_code == 409
    assert "already has your decision" in again.json()["detail"]


def test_api_refuses_a_decision_on_an_old_version(api, store, seeded):
    order(store, seeded)
    schedule = api.post("/api/schedules", json={"startDate": "2026-11-05", "endDate": "2026-11-05"}).json()["schedule"]
    revised = api.post(f"/api/schedules/{schedule['recordId']}/revisions",
                       json={"startDate": "2026-11-05", "endDate": "2026-11-05"})
    assert revised.json()["schedule"]["version"] == 2

    stale = api.post(f"/api/schedules/{schedule['recordId']}/decision", json={"version": 1, "decision": "Approved"})

    assert stale.status_code == 409
    assert "newer version" in stale.json()["detail"]
    missing = api.post(f"/api/schedules/{uuid.uuid4()}/decision", json={"version": 1, "decision": "Approved"})
    assert missing.status_code == 404
