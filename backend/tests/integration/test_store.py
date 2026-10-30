"""BAKE-7: ValidatedRecordStore against PostgreSQL.

Jira tests: save/load round trip; guarded save rejects a stale version. Also:
drafts are separate from approvals, constraint failures save nothing, audit
entries are append-only, and the constraint guard serialises writers [R1].
"""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from app.shared import (
    AuditImmutableError,
    RecordRef,
    RecordValidationError,
    StaleVersionError,
)
from app.shared.records import (
    ApprovalRequest,
    AuditEntry,
    InventoryItem,
    Order,
    OrderItem,
    OrderStatus,
    Quote,
)

REQUIRED_AT = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)  # 02 Oct 12:00 Sydney (Fig. 10)


def draft_order(seeded) -> Order:
    return Order(
        customer_id=seeded.customer_id,
        required_at=REQUIRED_AT,
        budget=Decimal("50.00"),
        items=[
            OrderItem(
                product_id=seeded.products["Vanilla cupcake"],
                quantity=24,
                flavour="vanilla",
                theme="birthday",
                customisation="standard",
                resolved_recipe_id=seeded.recipes["Vanilla cupcake"],
            )
        ],
    )


def save_draft(store, seeded) -> RecordRef:
    with store.unit_of_work() as session:
        return store.commit(session, draft_order(seeded), actor_id=seeded.customer_user_id, action="order.draft")


def quote_for(order_ref: RecordRef, total: str) -> Quote:
    return Quote(
        order_id=order_ref.record_id,
        source_order_version=order_ref.version,
        material_cost=Decimal("12.00"),
        labour_cost=Decimal("45.00"),
        packaging_cost=Decimal("6.00"),
        additional_cost=Decimal("0.00"),
        target_margin=Decimal("0.3000"),
        total=Decimal(total),
    )


def audit_count(store, subject_id) -> int:
    with store.unit_of_work() as session:
        return session.scalar(
            select(func.count()).select_from(AuditEntry).where(AuditEntry.subject_id == subject_id)
        )


def test_order_round_trip_keeps_every_field(store, seeded):
    ref = save_draft(store, seeded)

    with store.unit_of_work() as session:
        order = store.load_ref(session, ref)
        item = order.items[0]
        assert ref == RecordRef("Order", order.order_id, 1)
        assert order.status == OrderStatus.DRAFT
        assert order.required_at == REQUIRED_AT
        assert order.budget == Decimal("50.00")
        assert (item.quantity, item.flavour, item.theme) == (24, "vanilla", "birthday")
        assert item.resolved_recipe_id == seeded.recipes["Vanilla cupcake"]


def test_unit_cost_keeps_four_decimals(store, seeded):
    with store.unit_of_work() as session:
        flour = store.load(session, InventoryItem, seeded.items["Flour"])
        assert flour.unit_cost == Decimal("0.0030")
        assert flour.quantity_on_hand * flour.unit_cost == Decimal("3.0000000")


def test_guarded_save_rejects_a_stale_version(store, seeded):
    ref = save_draft(store, seeded)
    later = REQUIRED_AT + timedelta(hours=2)

    # Two editors both read version 1; the first revision wins.
    with store.unit_of_work() as session:
        order = store.load(session, Order, ref.record_id)
        order.required_at = later
        new_ref = store.commit(
            session, order, actor_id=seeded.owner_id, action="order.reschedule",
            expected_version=1, revise=True,
        )
    assert new_ref.version == 2

    with pytest.raises(StaleVersionError):
        with store.unit_of_work() as session:
            order = store.load(session, Order, ref.record_id)
            order.budget = Decimal("1.00")
            store.commit(
                session, order, actor_id=seeded.owner_id, action="order.edit",
                expected_version=1, revise=True,
            )

    with store.unit_of_work() as session:
        order = store.load(session, Order, ref.record_id)
        assert (order.version, order.required_at, order.budget) == (2, later, Decimal("50.00"))
        with pytest.raises(StaleVersionError):
            store.load_ref(session, ref)
    assert audit_count(store, ref.record_id) == 2


def test_change_to_a_versioned_record_needs_the_version_read(store, seeded):
    ref = save_draft(store, seeded)

    with pytest.raises(ValueError, match="expected_version"):
        with store.unit_of_work() as session:
            order = store.load(session, Order, ref.record_id)
            order.status = OrderStatus.CANCELLED
            store.commit(session, order, actor_id=seeded.owner_id, action="order.cancel")


def test_row_versioned_quote_keeps_history_and_rejects_stale_revision(store, seeded):
    order_ref = save_draft(store, seeded)
    with store.unit_of_work() as session:
        v1 = store.commit(session, quote_for(order_ref, "90.00"), actor_id=seeded.owner_id, action="quote.draft")

    revision = quote_for(order_ref, "95.00")
    revision.quote_id = v1.record_id
    with store.unit_of_work() as session:
        v2 = store.commit(
            session, revision, actor_id=seeded.owner_id, action="quote.revise", expected_version=1
        )
    assert v2 == RecordRef("Quote", v1.record_id, 2)

    stale = quote_for(order_ref, "99.00")
    stale.quote_id = v1.record_id
    with pytest.raises(StaleVersionError):
        with store.unit_of_work() as session:
            store.commit(session, stale, actor_id=seeded.owner_id, action="quote.revise", expected_version=1)

    with store.unit_of_work() as session:
        assert store.load(session, Quote, v1.record_id).total == Decimal("95.00")
        assert store.load_ref(session, v1).total == Decimal("90.00")


def test_saving_a_draft_does_not_approve_it(store, seeded):
    order_ref = save_draft(store, seeded)
    with store.unit_of_work() as session:
        quote_ref = store.commit(session, quote_for(order_ref, "90.00"), actor_id=seeded.owner_id, action="quote.draft")
        assert session.scalar(select(func.count()).select_from(ApprovalRequest)) == 0

        request = ApprovalRequest(
            kind="Quote",
            subject_type=quote_ref.record_type,
            subject_id=quote_ref.record_id,
            subject_version=quote_ref.version,
        )
        store.commit(session, request, actor_id=seeded.owner_id, action="approval.request")
        assert request.decision is None

    # One request per (kind, subject, version).
    with pytest.raises(RecordValidationError) as error:
        with store.unit_of_work() as session:
            store.commit(
                session,
                ApprovalRequest(kind="Quote", subject_type="Quote", subject_id=quote_ref.record_id, subject_version=1),
                actor_id=seeded.owner_id,
                action="approval.request",
            )
    assert error.value.constraint == "uq_approval_requests_subject_version"


def test_constraint_failure_saves_nothing(store, seeded):
    with pytest.raises(RecordValidationError) as error:
        with store.unit_of_work() as session:
            flour = store.load(session, InventoryItem, seeded.items["Flour"])
            flour.quantity_on_hand = Decimal("-1")
            store.commit(
                session, flour, actor_id=seeded.owner_id, action="stock.adjust", expected_version=1
            )

    assert error.value.code == "VALIDATION_FAILED"
    assert error.value.constraint == "ck_inventory_items_on_hand_nonnegative"
    with store.unit_of_work() as session:
        assert store.load(session, InventoryItem, seeded.items["Flour"]).quantity_on_hand == Decimal("1000")
    assert audit_count(store, seeded.items["Flour"]) == 1  # only the seed entry


def test_commit_writes_an_audit_entry_with_before_and_after(store, seeded):
    ref = save_draft(store, seeded)
    with store.unit_of_work() as session:
        order = store.load(session, Order, ref.record_id)
        order.status = OrderStatus.QUOTED
        store.commit(session, order, actor_id=seeded.owner_id, action="order.quoted", expected_version=1)

    with store.unit_of_work() as session:
        entries = session.scalars(
            select(AuditEntry).where(AuditEntry.subject_id == ref.record_id).order_by(AuditEntry.created_at)
        ).all()
        created, quoted = entries
        assert (created.action, created.actor_id, created.before_data) == (
            "order.draft", seeded.customer_user_id, None,
        )
        assert created.after_data["items"][0]["quantity"] == 24
        assert (quoted.subject_type, quoted.subject_version) == ("Order", 1)
        assert quoted.before_data["status"] == "Draft"
        assert quoted.after_data["status"] == "Quoted"


def test_audit_entries_cannot_be_changed_or_deleted(store, seeded):
    with store.unit_of_work() as session:
        entry = session.scalars(select(AuditEntry)).first()
        entry_id = entry.audit_id

    with pytest.raises(AuditImmutableError):
        with store.unit_of_work() as session:
            session.get(AuditEntry, entry_id).action = "rewritten"
    with pytest.raises(AuditImmutableError):
        with store.unit_of_work() as session:
            session.delete(session.get(AuditEntry, entry_id))
    with pytest.raises(AuditImmutableError):
        with store.unit_of_work() as session:
            session.execute(update(AuditEntry).values(action="rewritten"))
    with pytest.raises(AuditImmutableError):
        with store.unit_of_work() as session:
            session.execute(delete(AuditEntry))

    with store.unit_of_work() as session:
        assert session.get(AuditEntry, entry_id).action == "seed.create"


def test_constraint_guard_serialises_stock_writers(store, seeded):
    """Two guarded writers each want 600 g of the 1,000 g flour: only one succeeds."""
    start = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def take_600_g():
        try:
            start.wait(timeout=5)
            with store.constraint_guard() as session:
                flour = store.load(session, InventoryItem, seeded.items["Flour"])
                if flour.quantity_on_hand < 600:
                    outcomes.append("refused")
                    return
                time.sleep(0.3)  # widen the race window
                flour.quantity_on_hand -= 600
                store.commit(
                    session, flour, actor_id=seeded.owner_id, action="stock.take",
                    expected_version=flour.version,
                )
                outcomes.append("taken")
        except BaseException as exc:  # surface thread failures in the test
            errors.append(exc)

    threads = [threading.Thread(target=take_600_g) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert sorted(outcomes) == ["refused", "taken"]
    with store.unit_of_work() as session:
        assert store.load(session, InventoryItem, seeded.items["Flour"]).quantity_on_hand == Decimal("400")


def test_snapshot_reads_one_state_and_refuses_writes(store, seeded):
    flour = seeded.items["Flour"]
    on_hand = select(InventoryItem.quantity_on_hand).where(InventoryItem.item_id == flour)

    with store.snapshot() as session:
        first = session.scalar(on_hand)
        with store.constraint_guard() as writer:
            item = store.load(writer, InventoryItem, flour, lock=True)
            item.quantity_on_hand -= 600
            store.commit(writer, item, actor_id=seeded.owner_id, action="stock.take",
                         expected_version=item.version, revise=True)
        # Committed meanwhile, but the snapshot still sees what it read first.
        assert session.scalar(on_hand) == first == Decimal("1000.000")

    with pytest.raises(DBAPIError, match="read-only"):
        with store.snapshot() as session:
            store.commit(session, draft_order(seeded), actor_id=seeded.owner_id, action="order.draft")

    with store.unit_of_work() as session:
        assert session.scalar(on_hand) == Decimal("400.000")
        store.commit(session, draft_order(seeded), actor_id=seeded.owner_id, action="order.draft")


def test_unknown_record_is_not_found(store, seeded):
    from app.shared import RecordNotFound

    with store.unit_of_work() as session:
        with pytest.raises(RecordNotFound):
            store.load(session, Order, uuid.uuid4())
