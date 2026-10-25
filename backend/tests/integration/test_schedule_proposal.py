"""BAKE-20: UC-04 steps 1-5 on PostgreSQL, with Mock model replies.

Jira: only confirmed orders without an unresolved shortage are planned; the
agent's proposal is checked for structure, confidence and IP-05 feasibility;
a malformed or low-confidence proposal gets at most two retries and then
nothing is saved (4a); an infeasible plan is explained to the owner (5a); a
valid Mock proposal is accepted. Architecture TX-PLAN-DRAFT: the draft is
rechecked under the shared guard and saved as a schedule version that commits
no stock until it is released.

Review before the PR: the save rechecks capacity and labour minutes changed
while the agent ran; a revision re-plans its own Scheduled orders, replaces
all of its released commitments and gives a task moved back to its released
time the released ID; a saved version is audited and cannot change.
"""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.inventory_production import InventoryService, PlanStatus, ProductionPlanner
from app.inventory_production.fixtures import GreedyMockPlanner, proposal_reply
from app.llm.gateway import MockLLMClient
from app.shared import RecordValidationError, UnauditedWriteError
from app.shared.records import (
    AuditEntry,
    BakerySettings,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductionSchedule,
    ProductionScheduleVersion,
    ProductionTask,
    TaskStatus,
)

SYDNEY = ZoneInfo("Australia/Sydney")
DAY = date(2026, 11, 5)


def at(hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SYDNEY)


def order(store, seeded, cupcakes=24, due=None, status=OrderStatus.CONFIRMED):
    """A vanilla cupcake order: 24 need 600 g flour and 90 minutes of work."""
    with store.unit_of_work() as session:
        record = Order(
            customer_id=seeded.customer_id,
            required_at=(due or at(12)).astimezone(UTC),
            status=status,
            collection_kind="before",
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=cupcakes)],
        )
        return store.commit(session, record, actor_id=seeded.owner_id, action="test.order")


def tasks_for(*plan):
    """Reply tasks from (order ref, kind, start, minutes) tuples."""
    return [
        {
            "order_id": str(ref.record_id),
            "task_kind": kind,
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(minutes=minutes)).isoformat(),
        }
        for ref, kind, start, minutes in plan
    ]


def figure10(ref, start=None):
    start = start or at(6)
    return tasks_for((ref, "bake", start, 60), (ref, "finish", start + timedelta(minutes=60), 30))


def requested_orders(client):
    request = json.loads(client.calls[0].rsplit("TaskRequest: ", 1)[1])
    return [o["order_id"] for o in request["inputData"]["orders"]]


def count(store, model):
    with store.unit_of_work() as session:
        return session.scalar(select(func.count()).select_from(model))


def propose(store, seeded, client, **kwargs):
    return ProductionPlanner(store, client).propose(DAY, DAY, actor_id=seeded.owner_id, **kwargs)


def test_valid_mock_proposal_is_saved_as_a_draft_version(store, seeded):
    first, second = order(store, seeded), order(store, seeded, cupcakes=12, due=at(14))

    outcome = propose(store, seeded, GreedyMockPlanner())

    assert outcome.status is PlanStatus.PROPOSED, outcome.problems
    assert (outcome.schedule_ref.record_type, outcome.schedule_ref.version) == ("ProductionSchedule", 1)
    flour = str(seeded.items["Flour"])
    with store.unit_of_work() as session:
        schedule = store.load(session, ProductionSchedule, outcome.schedule_ref.record_id)
        version = session.get(ProductionScheduleVersion, (schedule.schedule_id, 1))
        tasks = session.scalars(select(ProductionTask).order_by(ProductionTask.start_at)).all()
        actions = session.scalars(
            select(AuditEntry.action).where(AuditEntry.subject_type.in_(["ProductionSchedule", "ProductionTask"]))
        ).all()
        orders = [store.load(session, Order, ref.record_id) for ref in (first, second)]
        assert schedule.released_version is None  # a draft, awaiting review
        assert version.requirements_snapshot[str(first.record_id)][flour] == "600.000"
        assert version.requirements_snapshot[str(second.record_id)][flour] == "300.000"
        assert version.source_order_refs == [first.to_json(), second.to_json()]
        assert (version.daily_capacity_minutes, version.start_date) == (240, DAY)
        assert [(t.order_id, t.task_kind) for t in tasks] == [
            (first.record_id, "bake"), (first.record_id, "finish"),
            (second.record_id, "bake"), (second.record_id, "finish"),
        ]
        assert {t.status for t in tasks} == {TaskStatus.PLANNED}
        assert actions == ["schedule.draft_saved"] * 5
        assert [(o.status, o.version) for o in orders] == [(OrderStatus.CONFIRMED, 1)] * 2
        # Until it is released, the draft commits no stock (BAKE-19).
        assert InventoryService(store).committed_needs(session) == {}


def test_only_confirmed_orders_due_in_the_plan_are_candidates(store, seeded):
    planned = order(store, seeded)
    order(store, seeded, status=OrderStatus.DRAFT)
    order(store, seeded, due=at(12, day=DAY + timedelta(days=1)))
    client = GreedyMockPlanner()

    outcome = propose(store, seeded, client)

    assert outcome.status is PlanStatus.PROPOSED
    assert requested_orders(client) == [str(planned.record_id)]


def test_order_short_of_stock_is_left_out_and_explained(store, seeded):
    short = order(store, seeded, cupcakes=48, due=at(10))  # 1,200 g of the 1,000 g flour
    planned = order(store, seeded)
    client = GreedyMockPlanner()

    outcome = propose(store, seeded, client)

    assert outcome.status is PlanStatus.PROPOSED
    assert requested_orders(client) == [str(planned.record_id)]
    assert [(e.order_ref, e.reason) for e in outcome.excluded] == [(short, "Short of 200 g flour.")]


def test_orders_that_only_fit_separately_are_taken_earliest_due_first(store, seeded):
    early, later = order(store, seeded, due=at(10)), order(store, seeded, due=at(15))
    client = GreedyMockPlanner()

    outcome = propose(store, seeded, client)

    # Each needs 600 g; together 1,200 g against 1,000 g.
    assert requested_orders(client) == [str(early.record_id)]
    assert [e.order_ref for e in outcome.excluded] == [later]
    assert "together with orders due earlier" in outcome.excluded[0].reason


def test_malformed_reply_is_retried(store, seeded):
    ref = order(store, seeded)
    client = MockLLMClient(replies=["not a plan"], default_reply=proposal_reply(figure10(ref)))

    outcome = propose(store, seeded, client)

    assert outcome.status is PlanStatus.PROPOSED
    assert len(client.calls) == 2


def test_low_confidence_plan_is_not_saved_after_two_retries(store, seeded):
    ref = order(store, seeded)
    client = MockLLMClient(default_reply=proposal_reply(figure10(ref), confidence=0.3))

    outcome = propose(store, seeded, client)

    assert outcome.status is PlanStatus.NEEDS_REVISION
    assert len(client.calls) == 3  # the initial attempt and two retries (4a)
    assert "confidence (0.30) is too low" in outcome.problems[0]
    assert count(store, ProductionSchedule) == count(store, ProductionTask) == 0


def test_infeasible_plan_is_explained_to_the_owner(store, seeded):
    ref = order(store, seeded)
    overlapping = tasks_for((ref, "bake", at(6), 60), (ref, "finish", at(6, 45), 30))
    client = MockLLMClient(default_reply=proposal_reply(overlapping))

    outcome = propose(store, seeded, client)

    assert outcome.status is PlanStatus.NEEDS_REVISION
    assert len(client.calls) == 3
    assert any("overlap" in problem for problem in outcome.problems)
    assert count(store, ProductionSchedule) == 0


def test_no_candidates_means_no_model_call(store, seeded):
    order(store, seeded, status=OrderStatus.DRAFT)
    client = GreedyMockPlanner()

    outcome = propose(store, seeded, client)

    assert outcome.status is PlanStatus.NO_CANDIDATES
    assert client.calls == []


class ChangingClient:
    """Replies with ``reply`` after running ``change``, as if it happened while the agent ran."""

    def __init__(self, change, reply):
        self.change, self.reply, self.calls = change, reply, []

    def complete(self, prompt):
        if not self.calls:
            self.change()
        self.calls.append(prompt)
        return self.reply


def change_quantity(store, seeded, ref, cupcakes):
    with store.unit_of_work() as session:
        record = store.load(session, Order, ref.record_id)
        record.items[0].quantity = cupcakes
        store.commit(
            session, record, actor_id=seeded.owner_id, action="test.edit",
            expected_version=ref.version, revise=True,
        )


def test_order_changed_while_planning_is_not_saved(store, seeded):
    ref = order(store, seeded)
    client = ChangingClient(lambda: change_quantity(store, seeded, ref, 12), proposal_reply(figure10(ref)))

    outcome = propose(store, seeded, client)

    assert outcome.status is PlanStatus.NEEDS_REVISION
    assert "changed while the plan was prepared" in outcome.problems[0]
    assert count(store, ProductionSchedule) == 0


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ("capacity", "has 90 minutes of work; the team has 60."),
        ("labour", "gets 90 minutes of work but needs 180."),
    ],
)
def test_capacity_and_labour_changed_while_planning_are_rechecked(store, seeded, change, problem):
    ref = order(store, seeded)

    def edit():
        with store.constraint_guard() as session:
            if change == "capacity":
                settings = session.scalars(select(BakerySettings)).one()
                settings.default_daily_capacity_minutes = 60
                store.commit(
                    session, settings, actor_id=seeded.owner_id, action="test.capacity",
                    expected_version=settings.version, revise=True,
                )
            else:
                product = session.get(Product, seeded.products["Vanilla cupcake"])
                product.labour_minutes = Decimal("7.500")  # 24 cupcakes: 180 minutes
                store.commit(session, product, actor_id=seeded.owner_id, action="test.labour")

    outcome = propose(store, seeded, ChangingClient(edit, proposal_reply(figure10(ref))))

    assert outcome.status is PlanStatus.NEEDS_REVISION
    assert any(problem in p for p in outcome.problems), outcome.problems
    assert count(store, ProductionSchedule) == 0


def test_revision_is_the_next_version_and_unchanged_tasks_keep_their_identity(store, seeded):
    ref = order(store, seeded)
    first = propose(store, seeded, MockLLMClient(default_reply=proposal_reply(figure10(ref))))
    moved_finish = tasks_for((ref, "bake", at(6), 60), (ref, "finish", at(7, 30), 30))

    second = propose(
        store, seeded, MockLLMClient(default_reply=proposal_reply(moved_finish)),
        schedule_id=first.schedule_ref.record_id,
    )

    assert second.status is PlanStatus.PROPOSED, second.problems
    assert second.schedule_ref.version == 2
    with store.unit_of_work() as session:
        def logical(version):
            return {
                t.task_kind: t.logical_task_id
                for t in session.scalars(select(ProductionTask).where(ProductionTask.schedule_version == version))
            }
        v1, v2 = logical(1), logical(2)
        assert v1["bake"] == v2["bake"]
        assert v1["finish"] != v2["finish"]
        assert store.load(session, ProductionSchedule, first.schedule_ref.record_id).released_version is None


def release(store, seeded, outcome):
    """Stand-in for BAKE-21's guarded release of the version ``outcome`` saved."""
    with store.constraint_guard() as session:
        schedule = store.load(session, ProductionSchedule, outcome.schedule_ref.record_id)
        schedule.released_version = outcome.schedule_ref.version
        store.commit(
            session, schedule, actor_id=seeded.owner_id, action="test.release",
            expected_version=schedule.version,
        )


def test_revision_re_plans_its_scheduled_orders(store, seeded):
    ref = order(store, seeded)
    first = propose(store, seeded, GreedyMockPlanner())
    release(store, seeded, first)
    with store.constraint_guard() as session:  # as BAKE-21's release will
        record = store.load(session, Order, ref.record_id)
        record.status = OrderStatus.SCHEDULED
        store.commit(
            session, record, actor_id=seeded.owner_id, action="test.scheduled", expected_version=ref.version
        )
    client = GreedyMockPlanner()

    revision = propose(store, seeded, client, schedule_id=first.schedule_ref.record_id)

    assert revision.status is PlanStatus.PROPOSED, revision.problems
    assert requested_orders(client) == [str(ref.record_id)]
    # A new plan still leaves it to the plan it is scheduled in.
    assert propose(store, seeded, GreedyMockPlanner()).status is PlanStatus.NO_CANDIDATES


def test_revision_replaces_all_of_its_released_commitments(store, seeded):
    early, later = order(store, seeded, cupcakes=12, due=at(10)), order(store, seeded, due=at(12))
    first = propose(store, seeded, GreedyMockPlanner())
    release(store, seeded, first)  # commits 300 g + 600 g of the 1,000 g flour
    change_quantity(store, seeded, early, 24)
    change_quantity(store, seeded, later, 12)
    client = GreedyMockPlanner()

    revision = propose(store, seeded, client, schedule_id=first.schedule_ref.record_id)

    # 600 g + 300 g replace the released 900 g. Checked against the other
    # order's old 600 g, the early order alone would look 200 g short.
    assert revision.status is PlanStatus.PROPOSED, revision.problems
    assert requested_orders(client) == [str(early.record_id), str(later.record_id)]
    assert revision.excluded == ()


def test_revision_that_leaves_out_a_released_order_replaces_its_commitment_too(store, seeded):
    early, later = order(store, seeded, cupcakes=12, due=at(10)), order(store, seeded, cupcakes=12, due=at(12))
    first = propose(store, seeded, GreedyMockPlanner())
    release(store, seeded, first)  # commits 300 g + 300 g of the 1,000 g flour
    change_quantity(store, seeded, early, 40)  # 1,000 g
    change_quantity(store, seeded, later, 48)  # 1,200 g: short on its own

    revision = propose(store, seeded, GreedyMockPlanner(), schedule_id=first.schedule_ref.record_id)

    # The save, too, must not count the left-out order's old 300 g.
    assert revision.status is PlanStatus.PROPOSED, revision.problems
    assert [(e.order_ref.record_id, e.reason) for e in revision.excluded] == [
        (later.record_id, "Short of 200 g flour.")
    ]


def test_task_moved_back_to_its_released_time_gets_its_released_id(store, seeded):
    ref = order(store, seeded)
    first = propose(store, seeded, MockLLMClient(default_reply=proposal_reply(figure10(ref))))
    release(store, seeded, first)
    for start in (at(8), at(6)):  # version 2 moves both tasks; version 3 moves them back
        revision = propose(
            store, seeded, MockLLMClient(default_reply=proposal_reply(figure10(ref, start))),
            schedule_id=first.schedule_ref.record_id,
        )
        assert revision.status is PlanStatus.PROPOSED, revision.problems

    with store.unit_of_work() as session:
        def logical(version):
            return {
                t.task_kind: t.logical_task_id
                for t in session.scalars(select(ProductionTask).where(ProductionTask.schedule_version == version))
            }
        # Release carries progress over by these IDs [C1].
        assert logical(3) == logical(1)
        assert logical(2)["bake"] != logical(1)["bake"]


def test_saved_version_is_audited_and_cannot_change(store, seeded):
    ref = order(store, seeded)
    outcome = propose(store, seeded, GreedyMockPlanner())
    key = (outcome.schedule_ref.record_id, 1)
    with store.unit_of_work() as session:
        audit = session.scalars(
            select(AuditEntry).where(AuditEntry.subject_type == "ProductionScheduleVersion")
        ).one()
        snapshot = session.get(ProductionScheduleVersion, key).requirements_snapshot
        assert audit.after_data["requirements_snapshot"] == snapshot
        assert list(snapshot) == [str(ref.record_id)]

    # Emptying the snapshot of a released version would free its stock silently.
    with pytest.raises(UnauditedWriteError):
        with store.unit_of_work() as session:
            session.get(ProductionScheduleVersion, key).requirements_snapshot = {}
    with pytest.raises(RecordValidationError, match="cannot change"):
        with store.unit_of_work() as session:
            version = session.get(ProductionScheduleVersion, key)
            version.requirements_snapshot = {}
            store.commit(session, version, actor_id=seeded.owner_id, action="test.edit")

    with store.unit_of_work() as session:
        assert session.get(ProductionScheduleVersion, key).requirements_snapshot == snapshot


def released_elsewhere(store, seeded, ref, start, minutes):
    """Another schedule whose released version holds ``ref`` from ``start``."""
    with store.unit_of_work() as session:
        schedule = ProductionSchedule(created_by=seeded.owner_id, released_version=1)
        store.commit(session, schedule, actor_id=seeded.owner_id, action="test.schedule")
        version = ProductionScheduleVersion(
            schedule_id=schedule.schedule_id, version=1, start_date=DAY, end_date=DAY,
            daily_capacity_minutes=240,
            requirements_snapshot={str(ref.record_id): {str(seeded.items["Flour"]): "300.000"}},
            source_order_refs=[ref.to_json()],
        )
        store.commit(session, version, actor_id=seeded.owner_id, action="test.version")
        task = ProductionTask(
            schedule_id=schedule.schedule_id, schedule_version=1, order_id=ref.record_id,
            task_kind="bake", start_at=start, end_at=start + timedelta(minutes=minutes),
        )
        store.commit(session, task, actor_id=seeded.owner_id, action="test.task")


@pytest.mark.parametrize("client_kind", ["overlapping", "mock"])
def test_released_plans_keep_their_orders_and_the_team_busy(store, seeded, client_kind):
    other = order(store, seeded, cupcakes=12)
    released_elsewhere(store, seeded, other, at(6), 90)  # 06:00-07:30
    ref = order(store, seeded)
    client = (
        MockLLMClient(default_reply=proposal_reply(figure10(ref)))
        if client_kind == "overlapping"
        else GreedyMockPlanner()
    )

    outcome = propose(store, seeded, client)

    assert [e.order_ref for e in outcome.excluded] == [other]  # already planned
    if client_kind == "overlapping":
        assert outcome.status is PlanStatus.NEEDS_REVISION
        assert any("overlap" in problem for problem in outcome.problems)
    else:
        assert outcome.status is PlanStatus.PROPOSED
        assert outcome.tasks[0].start_at.astimezone(SYDNEY) == at(7, 30)
