"""BAKE-7 review (PR #8): versions cannot be bypassed and audits match the database.

1. A saved quote/campaign/content version cannot be rewritten in place, and a
   new row cannot jump to an arbitrary version.
2. Reading other records before ``commit()`` does not write changes early, and
   a change that skips ``commit()`` is refused rather than saved unaudited.
3. Owned rows that were never loaded (order items) are still audited.
4. Values are checked against the column precision before saving, so the
   audit records exactly what the database stores.

Second review round:

5. Only the store sets ``version``; a caller cannot roll it back, and the
   loaded object must be the version the caller claims to have read.
6. Bulk INSERT/UPDATE/DELETE statements on business tables are refused, so
   they cannot skip version checks and audit.
7. Every column type is checked, not only money: integers, booleans, text,
   UUIDs, dates and JSON.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, insert, select, update

from app.shared import RecordValidationError, StaleVersionError, UnauditedWriteError
from app.shared.records import (
    AuditEntry,
    CustomerDecision,
    InventoryItem,
    Order,
    OrderItem,
    Product,
    Quote,
)

from test_store import draft_order, quote_for, save_draft  # noqa: E402


def saved_quote(store, seeded, total="90.00"):
    order_ref = save_draft(store, seeded)
    with store.unit_of_work() as session:
        return order_ref, store.commit(
            session, quote_for(order_ref, total), actor_id=seeded.owner_id, action="quote.draft"
        )


def latest_audit(store, subject_id) -> AuditEntry:
    with store.unit_of_work() as session:
        return session.scalars(
            select(AuditEntry)
            .where(AuditEntry.subject_id == subject_id)
            .order_by(AuditEntry.created_at.desc())
            .limit(1)
        ).one()


# 1. Row-versioned records -------------------------------------------------


@pytest.mark.parametrize("expected_version", [None, 1])
def test_saved_quote_content_cannot_be_rewritten_in_place(store, seeded, expected_version):
    _, quote_ref = saved_quote(store, seeded)

    with pytest.raises(RecordValidationError, match="total"):
        with store.unit_of_work() as session:
            quote = store.load_ref(session, quote_ref)
            quote.total = Decimal("999.00")
            store.commit(
                session, quote, actor_id=seeded.owner_id, action="quote.edit",
                expected_version=expected_version,
            )

    with store.unit_of_work() as session:
        quote = store.load(session, Quote, quote_ref.record_id)
        assert (quote.version, quote.total) == (1, Decimal("90.00"))


def test_new_quote_row_cannot_jump_to_any_version(store, seeded):
    order_ref, quote_ref = saved_quote(store, seeded)

    jump = quote_for(order_ref, "95.00")
    jump.quote_id, jump.version = quote_ref.record_id, 99
    with pytest.raises(ValueError, match="version"):
        with store.unit_of_work() as session:
            store.commit(session, jump, actor_id=seeded.owner_id, action="quote.revise")

    wrong = quote_for(order_ref, "95.00")
    wrong.quote_id, wrong.version = quote_ref.record_id, 5
    with pytest.raises(ValueError, match="version"):
        with store.unit_of_work() as session:
            store.commit(session, wrong, actor_id=seeded.owner_id, action="quote.revise", expected_version=1)

    with store.unit_of_work() as session:
        assert store.load(session, Quote, quote_ref.record_id).version == 1


def test_customer_decision_is_recorded_on_the_latest_version_only(store, seeded):
    order_ref, v1 = saved_quote(store, seeded)
    revision = quote_for(order_ref, "95.00")
    revision.quote_id = v1.record_id
    with store.unit_of_work() as session:
        v2 = store.commit(session, revision, actor_id=seeded.owner_id, action="quote.revise", expected_version=1)

    with pytest.raises(StaleVersionError):
        with store.unit_of_work() as session:
            old = store.load_ref(session, v1)
            old.customer_decision = CustomerDecision.ACCEPTED
            store.commit(session, old, actor_id=seeded.customer_user_id, action="quote.accept", expected_version=1)

    with pytest.raises(ValueError, match="expected_version"):
        with store.unit_of_work() as session:
            current = store.load_ref(session, v2)
            current.customer_decision = CustomerDecision.ACCEPTED
            store.commit(session, current, actor_id=seeded.customer_user_id, action="quote.accept")

    with store.unit_of_work() as session:
        current = store.load_ref(session, v2)
        current.customer_decision = CustomerDecision.ACCEPTED
        ref = store.commit(
            session, current, actor_id=seeded.customer_user_id, action="quote.accept", expected_version=2
        )
    assert ref == v2  # a decision does not create a new version

    audit = latest_audit(store, v1.record_id)
    assert (audit.before_data["customer_decision"], audit.after_data["customer_decision"]) == (
        "Pending", "Accepted",
    )


# 2. No early or unaudited writes --------------------------------------------


def test_reading_other_records_does_not_write_changes_early(store, seeded):
    ref = save_draft(store, seeded)

    with store.unit_of_work() as session:
        order = store.load(session, Order, ref.record_id)
        order.budget = Decimal("60.00")
        store.load(session, InventoryItem, seeded.items["Flour"])  # an unrelated read
        store.commit(session, order, actor_id=seeded.owner_id, action="order.edit", expected_version=1)

    audit = latest_audit(store, ref.record_id)
    assert (audit.before_data["budget"], audit.after_data["budget"]) == ("50.00", "60.00")


def test_change_without_commit_is_refused_and_not_saved(store, seeded):
    ref = save_draft(store, seeded)

    with pytest.raises(UnauditedWriteError):
        with store.unit_of_work() as session:
            store.load(session, Order, ref.record_id).budget = Decimal("1.00")

    with store.unit_of_work() as session:
        assert store.load(session, Order, ref.record_id).budget == Decimal("50.00")


def test_commit_refuses_to_carry_another_records_pending_change(store, seeded):
    ref = save_draft(store, seeded)

    with pytest.raises(UnauditedWriteError, match="InventoryItem"):
        with store.unit_of_work() as session:
            store.load(session, InventoryItem, seeded.items["Flour"]).quantity_on_hand = Decimal("1")
            order = store.load(session, Order, ref.record_id)
            order.budget = Decimal("60.00")
            store.commit(session, order, actor_id=seeded.owner_id, action="order.edit", expected_version=1)

    with store.unit_of_work() as session:
        assert store.load(session, InventoryItem, seeded.items["Flour"]).quantity_on_hand == Decimal("1000")
        assert store.load(session, Order, ref.record_id).budget == Decimal("50.00")


# 3. Unloaded owned rows ---------------------------------------------------------


def test_unloaded_order_items_are_still_audited(store, seeded):
    ref = save_draft(store, seeded)

    with store.unit_of_work() as session:
        order = store.load(session, Order, ref.record_id)  # items not touched
        order.budget = Decimal("55.00")
        store.commit(session, order, actor_id=seeded.owner_id, action="order.edit", expected_version=1)

    audit = latest_audit(store, ref.record_id)
    assert [item["quantity"] for item in audit.before_data["items"]] == [24]
    assert [item["quantity"] for item in audit.after_data["items"]] == [24]


# 4. Precision and time zones ------------------------------------------------------


@pytest.mark.parametrize("budget", [Decimal("10.005"), 10.5, Decimal("1E+11")])
def test_money_must_fit_the_column_exactly(store, seeded, budget):
    order = draft_order(seeded)
    order.budget = budget

    with pytest.raises(RecordValidationError, match="budget"):
        with store.unit_of_work() as session:
            store.commit(session, order, actor_id=seeded.customer_user_id, action="order.draft")

    with store.unit_of_work() as session:
        assert session.scalars(select(Order)).first() is None


def test_audit_records_the_stored_value(store, seeded):
    order = draft_order(seeded)
    order.budget = Decimal("10.000")  # same value as 10.00

    with store.unit_of_work() as session:
        ref = store.commit(session, order, actor_id=seeded.customer_user_id, action="order.draft")

    audit = latest_audit(store, ref.record_id)
    with store.unit_of_work() as session:
        stored = store.load(session, Order, ref.record_id).budget
    assert audit.after_data["budget"] == str(stored) == "10.00"


def test_timestamps_must_carry_a_time_zone(store, seeded):
    order = draft_order(seeded)
    order.required_at = datetime(2026, 10, 2, 12, 0)  # naive

    with pytest.raises(RecordValidationError, match="required_at"):
        with store.unit_of_work() as session:
            store.commit(session, order, actor_id=seeded.customer_user_id, action="order.draft")


# 5. Versions are managed by the store ------------------------------------------


def revise_budget(store, seeded, order_id, budget, expected):
    with store.unit_of_work() as session:
        order = store.load(session, Order, order_id)
        order.budget = Decimal(budget)
        return store.commit(
            session, order, actor_id=seeded.owner_id, action="order.edit",
            expected_version=expected, revise=True,
        )


def test_caller_cannot_roll_a_version_back(store, seeded):
    v1 = save_draft(store, seeded)
    revise_budget(store, seeded, v1.record_id, "60.00", expected=1)

    with pytest.raises(ValueError, match="version"):
        with store.unit_of_work() as session:
            order = store.load(session, Order, v1.record_id)
            order.version = 1
            order.budget = Decimal("70.00")
            store.commit(session, order, actor_id=seeded.owner_id, action="order.edit", expected_version=2)

    with store.unit_of_work() as session:
        order = store.load(session, Order, v1.record_id)
        assert (order.version, order.budget) == (2, Decimal("60.00"))
        with pytest.raises(StaleVersionError):
            store.load_ref(session, v1)


def test_caller_cannot_change_a_saved_rows_version(store, seeded):
    _, quote_ref = saved_quote(store, seeded)

    with pytest.raises(ValueError, match="version"):
        with store.unit_of_work() as session:
            quote = store.load_ref(session, quote_ref)
            quote.version = 7
            store.commit(session, quote, actor_id=seeded.owner_id, action="quote.edit", expected_version=1)


def test_object_loaded_before_another_revision_is_stale(store, seeded):
    v1 = save_draft(store, seeded)

    with pytest.raises(StaleVersionError):
        with store.unit_of_work() as session:
            old = store.load(session, Order, v1.record_id)  # reads version 1
            revise_budget(store, seeded, v1.record_id, "60.00", expected=1)  # someone else: v2
            old.budget = Decimal("99.00")
            # Claims to have read v2, but this object holds version 1's content.
            store.commit(session, old, actor_id=seeded.owner_id, action="order.edit", expected_version=2)

    with store.unit_of_work() as session:
        assert store.load(session, Order, v1.record_id).budget == Decimal("60.00")


# 6. No bulk writes on business tables -------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        lambda s: update(Quote).values(total=Decimal("999.00")),
        lambda s: update(Quote.__table__).values(total=Decimal("999.00")),
        lambda s: delete(Order),
        lambda s: insert(Product).values(
            name="Ghost cake", category="cake", labour_minutes=Decimal("1"), packaging_cost=Decimal("1")
        ),
    ],
    ids=["orm-update", "core-update", "delete", "insert"],
)
def test_bulk_writes_to_business_tables_are_refused(store, seeded, statement):
    order_ref, quote_ref = saved_quote(store, seeded)
    with store.unit_of_work() as session:
        audits = session.scalar(select(func.count()).select_from(AuditEntry))

    with pytest.raises(UnauditedWriteError):
        with store.unit_of_work() as session:
            session.execute(statement(seeded))

    with store.unit_of_work() as session:
        assert store.load_ref(session, quote_ref).total == Decimal("90.00")
        assert store.load_ref(session, order_ref) is not None
        assert session.scalar(select(func.count()).select_from(Product)) == 4
        assert session.scalar(select(func.count()).select_from(AuditEntry)) == audits


# 7. Every column type is checked ------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", Decimal("1.5")),  # the database would round it to 2
        ("quantity", Decimal("2")),
        ("quantity", True),
        ("quantity", 2**31),
        ("flavour", 42),
    ],
)
def test_order_item_fields_must_fit_their_columns(store, seeded, field, value):
    order = draft_order(seeded)
    setattr(order.items[0], field, value)

    with pytest.raises(RecordValidationError, match=field):
        with store.unit_of_work() as session:
            store.commit(session, order, actor_id=seeded.customer_user_id, action="order.draft")

    with store.unit_of_work() as session:
        assert session.scalar(select(func.count()).select_from(OrderItem)) == 0
