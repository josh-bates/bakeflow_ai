"""BAKE-32: an owner's ingredient receipt raises stock once (UC-03 7a), on PostgreSQL.

Jira acceptance (architecture §11): an owner-only receipt raises stock once
under the shared constraint guard [R1] with an audited source record [R12];
repeating the same receipt cannot increase stock twice. Architecture T-AUTH:
stock receipts need the owner, server-side. IP-02: received stock is
available to the next stock check, after what released plans commit.
"""

import threading
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.access import Identity, require_owner
from app.config import Settings
from app.inventory_production import (
    InventoryService,
    ReceiptConflict,
    ReceiptInput,
    ReceiptRejected,
)
from app.inventory_production.inventory import RECEIPT_ACTION, RECEIVE_ACTION
from app.inventory_production.routes import make_router
from app.main import create_app
from app.shared import RecordNotFound
from app.shared.records import (
    AuditEntry,
    InventoryItem,
    InventoryReceipt,
    Order,
    OrderItem,
    OrderStatus,
    ProductionSchedule,
    ProductionScheduleVersion,
)

REQUIRED_AT = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)


@pytest.fixture
def inventory(store) -> InventoryService:
    return InventoryService(store)


def flour_receipt(seeded, quantity="2500", reference="INV-1001", **kwargs) -> ReceiptInput:
    return ReceiptInput(
        item_id=kwargs.pop("item_id", seeded.items["Flour"]),
        quantity=Decimal(quantity) if isinstance(quantity, str) else quantity,
        source_type=kwargs.pop("source_type", "Invoice"),
        source_reference=reference,
    )


def on_hand(store, item_id) -> tuple[Decimal, int]:
    with store.unit_of_work() as session:
        item = session.get(InventoryItem, item_id)
        return item.quantity_on_hand, item.version


def receipts(store) -> list[InventoryReceipt]:
    with store.unit_of_work() as session:
        return list(
            session.scalars(
                select(InventoryReceipt).order_by(
                    InventoryReceipt.source_reference, InventoryReceipt.source_type
                )
            )
        )


def audits(store, action) -> list[AuditEntry]:
    with store.unit_of_work() as session:
        return list(session.scalars(select(AuditEntry).where(AuditEntry.action == action)))


def test_receipt_raises_stock_once_with_an_audited_source_record(inventory, store, seeded):
    flour = seeded.items["Flour"]

    saved = inventory.receive_stock(flour_receipt(seeded), actor_id=seeded.owner_id)

    # Seeded 1,000 g plus the 2,500 g delivered.
    assert on_hand(store, flour) == (Decimal("3500.000"), 2)
    assert (saved.quantity, saved.on_hand, saved.replayed) == (
        Decimal("2500.000"), Decimal("3500.000"), False,
    )
    assert saved.item_ref.version == 2
    [row] = receipts(store)
    assert row.receipt_id == saved.receipt_id
    assert (row.item_id, row.quantity, row.source_type, row.source_reference, row.actor_id) == (
        flour, Decimal("2500.000"), "Invoice", "INV-1001", seeded.owner_id,
    )
    assert row.received_at.tzinfo is not None
    [recorded] = audits(store, RECEIPT_ACTION)
    assert (recorded.subject_type, recorded.subject_id, recorded.actor_id) == (
        "InventoryReceipt", saved.receipt_id, seeded.owner_id,
    )
    assert recorded.after_data["source_reference"] == "INV-1001"
    [raised] = audits(store, RECEIVE_ACTION)
    assert (raised.subject_type, raised.subject_id, raised.subject_version) == ("InventoryItem", flour, 2)
    assert raised.before_data["quantity_on_hand"] == "1000.000"
    assert raised.after_data["quantity_on_hand"] == "3500.000"


def test_repeating_a_receipt_returns_it_without_a_second_increase(inventory, store, seeded):
    first = inventory.receive_stock(flour_receipt(seeded), actor_id=seeded.owner_id)

    # The same invoice, typed again with different spacing and case.
    repeat = inventory.receive_stock(
        flour_receipt(seeded, "2500.000", reference="  inv-1001 "), actor_id=seeded.owner_id
    )

    assert repeat.replayed
    assert repeat.receipt_id == first.receipt_id
    assert repeat.on_hand == Decimal("3500.000")
    assert on_hand(store, seeded.items["Flour"]) == (Decimal("3500.000"), 2)
    assert len(receipts(store)) == 1
    assert len(audits(store, RECEIPT_ACTION)) == 1
    assert len(audits(store, RECEIVE_ACTION)) == 1


def test_concurrent_repeats_of_one_receipt_raise_stock_once(inventory, store, seeded):
    start = threading.Barrier(4)
    outcomes = []

    def submit():
        start.wait()
        try:
            outcomes.append(inventory.receive_stock(flour_receipt(seeded), actor_id=seeded.owner_id))
        except Exception as exc:  # reported by the assertions below
            outcomes.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(not isinstance(outcome, Exception) for outcome in outcomes), outcomes
    assert sorted(outcome.replayed for outcome in outcomes) == [False, True, True, True]
    assert len({outcome.receipt_id for outcome in outcomes}) == 1
    assert on_hand(store, seeded.items["Flour"]) == (Decimal("3500.000"), 2)
    assert len(receipts(store)) == 1


def test_same_reference_with_a_different_quantity_is_refused(inventory, store, seeded):
    first = inventory.receive_stock(flour_receipt(seeded), actor_id=seeded.owner_id)

    with pytest.raises(ReceiptConflict) as error:
        inventory.receive_stock(flour_receipt(seeded, "3000"), actor_id=seeded.owner_id)

    assert error.value.code == "CONFLICT"
    assert error.value.existing.receipt_id == first.receipt_id
    assert error.value.existing.quantity == Decimal("2500.000")
    assert on_hand(store, seeded.items["Flour"]) == (Decimal("3500.000"), 2)
    assert len(receipts(store)) == 1


def test_one_invoice_can_deliver_several_ingredients(inventory, store, seeded):
    sugar = seeded.items["Caster sugar"]
    inventory.receive_stock(flour_receipt(seeded), actor_id=seeded.owner_id)
    inventory.receive_stock(flour_receipt(seeded, "500", item_id=sugar), actor_id=seeded.owner_id)
    # A delivery note numbered like the invoice is a different source.
    inventory.receive_stock(
        flour_receipt(seeded, "100", source_type="DeliveryNote"), actor_id=seeded.owner_id
    )

    assert on_hand(store, seeded.items["Flour"]) == (Decimal("3600.000"), 3)
    assert on_hand(store, sugar) == (Decimal("2500.000"), 2)
    assert len(receipts(store)) == 3


@pytest.mark.parametrize(
    "quantity",
    [Decimal("0"), Decimal("-5"), Decimal("0.0005"), Decimal("2.5005"), 2.5, Decimal("NaN"),
     Decimal("Infinity"), Decimal("1000000000")],
)
def test_quantity_must_be_positive_and_exact(inventory, store, seeded, quantity):
    with pytest.raises(ReceiptRejected) as error:
        inventory.receive_stock(flour_receipt(seeded, quantity), actor_id=seeded.owner_id)

    assert [issue.field for issue in error.value.issues] == ["quantity"]
    assert on_hand(store, seeded.items["Flour"]) == (Decimal("1000.000"), 1)
    assert receipts(store) == []
    assert audits(store, RECEIPT_ACTION) == []


def test_trailing_zeros_are_exact(inventory, store, seeded):
    saved = inventory.receive_stock(flour_receipt(seeded, "0.2500"), actor_id=seeded.owner_id)
    assert saved.quantity == Decimal("0.250")
    assert on_hand(store, seeded.items["Flour"])[0] == Decimal("1000.250")


def test_source_must_be_known_and_referenced(inventory, store, seeded):
    with pytest.raises(ReceiptRejected) as error:
        inventory.receive_stock(
            flour_receipt(seeded, reference="   ", source_type="invoice"), actor_id=seeded.owner_id
        )
    assert {issue.field for issue in error.value.issues} == {"source_type", "source_reference"}
    assert receipts(store) == []


def test_stock_cannot_rise_past_its_column(inventory, store, seeded):
    with pytest.raises(ReceiptRejected):
        inventory.receive_stock(flour_receipt(seeded, "999999999.999"), actor_id=seeded.owner_id)
    assert on_hand(store, seeded.items["Flour"]) == (Decimal("1000.000"), 1)


def test_unknown_item_is_not_found(inventory, store, seeded):
    with pytest.raises(RecordNotFound):
        inventory.receive_stock(flour_receipt(seeded, item_id=uuid.uuid4()), actor_id=seeded.owner_id)
    assert receipts(store) == []


def vanilla_order(store, seeded, status=OrderStatus.CONFIRMED):
    """24 vanilla cupcakes: 600 g of the seeded 1,000 g flour (Fig. 10)."""
    with store.unit_of_work() as session:
        order = Order(
            customer_id=seeded.customer_id,
            required_at=REQUIRED_AT,
            status=status,
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=24)],
        )
        return store.commit(session, order, actor_id=seeded.owner_id, action="test.order")


def released_plan(store, seeded, order_ref, flour_grams):
    """A schedule whose released version 1 commits ``flour_grams`` to the order [C1]."""
    with store.unit_of_work() as session:
        schedule = ProductionSchedule(created_by=seeded.owner_id)
        store.commit(session, schedule, actor_id=seeded.owner_id, action="test.schedule")
        version = ProductionScheduleVersion(
            schedule_id=schedule.schedule_id,
            version=1,
            start_date=date(2026, 10, 30),
            end_date=date(2026, 10, 30),
            daily_capacity_minutes=240,
            requirements_snapshot={
                str(order_ref.record_id): {str(seeded.items["Flour"]): flour_grams}
            },
            source_order_refs=[order_ref.to_json()],
        )
        store.commit(session, version, actor_id=seeded.owner_id, action="test.version")
        schedule.released_version = 1
        store.commit(
            session, schedule, actor_id=seeded.owner_id, action="test.release", expected_version=1
        )


def test_receipt_covers_a_shortage_left_by_a_released_plan(inventory, store, seeded):
    flour = seeded.items["Flour"]
    released_plan(store, seeded, vanilla_order(store, seeded), "600.000")
    candidate = vanilla_order(store, seeded)

    def flour_shortage():
        result = inventory.run_stock_check(
            candidate.record_id, expected_order_version=candidate.version, actor_id=seeded.owner_id
        )
        return result.shortages.get(flour)

    # 1,000 g on hand, 600 g committed: the second order is 200 g short.
    assert flour_shortage() == Decimal("200.000")
    [level] = [level for level in inventory.stock_levels() if level.item_id == flour]
    assert (level.on_hand, level.committed, level.available) == (
        Decimal("1000.000"), Decimal("600.000"), Decimal("400.000"),
    )

    inventory.receive_stock(flour_receipt(seeded, "200"), actor_id=seeded.owner_id)

    assert flour_shortage() is None
    [level] = [level for level in inventory.stock_levels() if level.item_id == flour]
    assert (level.on_hand, level.committed, level.available) == (
        Decimal("1200.000"), Decimal("600.000"), Decimal("600.000"),
    )


def test_stock_levels_read_one_state_while_a_deduction_commits(store, seeded):
    """Stock on hand and commitments come from the same snapshot (review of #33)."""
    flour = seeded.items["Flour"]
    order_ref = vanilla_order(store, seeded, status=OrderStatus.IN_PRODUCTION)
    released_plan(store, seeded, order_ref, "600.000")
    errors, finished_between_reads = [], []

    def deduct():
        """TX-DEDUCT in short: 600 g leaves stock and the order completes, together."""
        try:
            with store.constraint_guard() as session:
                item = store.load(session, InventoryItem, flour, lock=True)
                item.quantity_on_hand -= Decimal("600.000")
                store.commit(session, item, actor_id=seeded.owner_id, action="test.deduct",
                             expected_version=item.version, revise=True)
                order = store.load(session, Order, order_ref.record_id, lock=True)
                order.status = OrderStatus.COMPLETED
                store.commit(session, order, actor_id=seeded.owner_id, action="test.complete",
                             expected_version=order.version)
        except Exception as exc:  # reported by the assertions below
            errors.append(exc)

    class DeductionBetweenReads(InventoryService):
        def committed_needs(self, session, **kwargs):
            # Stock on hand is read; the deduction commits before the commitments are.
            writer = threading.Thread(target=deduct)
            writer.start()
            writer.join(timeout=10)
            finished_between_reads.append(not writer.is_alive())
            return super().committed_needs(session, **kwargs)

    [level] = [lv for lv in DeductionBetweenReads(store).stock_levels() if lv.item_id == flour]

    assert finished_between_reads == [True] and errors == []
    # Before the deduction, not the old 1,000 g next to no commitment.
    assert (level.on_hand, level.committed, level.available) == (
        Decimal("1000.000"), Decimal("600.000"), Decimal("400.000"),
    )
    [after] = [lv for lv in InventoryService(store).stock_levels() if lv.item_id == flour]
    assert (after.on_hand, after.committed, after.available) == (
        Decimal("400.000"), Decimal("0.000"), Decimal("400.000"),
    )


def test_reference_length_is_checked_as_stored(inventory, store, seeded):
    # Upper-casing turns "ß" into "SS": 101 of them would store 202 characters.
    with pytest.raises(ReceiptRejected) as error:
        inventory.receive_stock(flour_receipt(seeded, reference="ß" * 101), actor_id=seeded.owner_id)
    assert [issue.field for issue in error.value.issues] == ["source_reference"]
    assert receipts(store) == []

    saved = inventory.receive_stock(flour_receipt(seeded, reference="ß" * 100), actor_id=seeded.owner_id)
    assert saved.source_reference == "SS" * 100


def test_stock_levels_list_every_ingredient_by_name(inventory, seeded):
    levels = inventory.stock_levels()
    assert [level.ingredient for level in levels] == sorted(seeded.items)
    assert all(level.committed == Decimal("0.000") for level in levels)
    assert all(level.available == level.on_hand for level in levels)


@pytest.fixture
def api(store, seeded) -> TestClient:
    app = FastAPI()
    app.include_router(make_router(Settings(_env_file=None), store=store))
    app.dependency_overrides[require_owner] = lambda: Identity(seeded.owner_id, "Owner", "Owner", None)
    return TestClient(app)


def body(seeded, **changes) -> dict:
    return {"itemId": str(seeded.items["Flour"]), "quantity": "2500", "sourceType": "Invoice",
            "sourceReference": "INV-1001"} | changes


def test_api_records_replays_and_refuses_a_changed_quantity(api, store, seeded):
    created = api.post("/api/inventory/receipts", json=body(seeded))
    assert created.status_code == 201
    assert created.json() | {"id": None, "receivedAt": None} == {
        "id": None, "itemId": str(seeded.items["Flour"]), "ingredient": "Flour", "unit": "g",
        "quantity": "2500.000", "sourceType": "Invoice", "sourceReference": "INV-1001",
        "receivedAt": None, "onHand": "3500.000", "replayed": False,
    }

    replayed = api.post("/api/inventory/receipts", json=body(seeded, quantity="2500.000"))
    assert replayed.status_code == 200
    assert replayed.json()["id"] == created.json()["id"]
    assert replayed.json()["replayed"] is True

    changed = api.post("/api/inventory/receipts", json=body(seeded, quantity="2400"))
    assert changed.status_code == 409
    assert changed.json()["existing"]["id"] == created.json()["id"]
    assert "Invoice INV-1001 already recorded 2500.000 g of Flour" in changed.json()["detail"]

    listed = {item["ingredient"]: item for item in api.get("/api/inventory/items").json()["items"]}
    assert listed["Flour"] | {"id": None} == {
        "id": None, "ingredient": "Flour", "unit": "g",
        "onHand": "3500.000", "committed": "0.000", "available": "3500.000",
        # BAKE-37: the seeded reorder level; the receipt revised the item.
        "reorderLevel": "200.000", "version": 2, "low": False, "suggestedReorder": None,
    }
    assert on_hand(store, seeded.items["Flour"]) == (Decimal("3500.000"), 2)


@pytest.mark.parametrize("quantity", [2500, 2500.5, None])
def test_api_refuses_a_quantity_that_is_not_a_decimal_string(api, store, seeded, quantity):
    response = api.post("/api/inventory/receipts", json=body(seeded, quantity=quantity))
    assert response.status_code == 422
    assert receipts(store) == []


def test_api_lists_what_needs_fixing(api, store, seeded):
    response = api.post(
        "/api/inventory/receipts", json=body(seeded, quantity="1.2345", sourceType="Receipt")
    )
    assert response.status_code == 422
    assert {issue["field"] for issue in response.json()["issues"]} == {"quantity", "source_type"}
    assert receipts(store) == []


def test_api_reference_too_long_once_upper_cased_is_422(api, store, seeded):
    response = api.post("/api/inventory/receipts", json=body(seeded, sourceReference="ß" * 101))
    assert response.status_code == 422
    assert [issue["field"] for issue in response.json()["issues"]] == ["source_reference"]
    assert on_hand(store, seeded.items["Flour"]) == (Decimal("1000.000"), 1)


def test_api_unknown_item_is_404(api, seeded):
    response = api.post("/api/inventory/receipts", json=body(seeded, itemId=str(uuid.uuid4())))
    assert response.status_code == 404


@pytest.fixture
def app(database_url, seeded):
    return create_app(Settings(_env_file=None, database_url=database_url,
                              environment="test", app_secret="test-secret-" * 4))


def login(client, role):
    response = client.post("/api/auth/login", json={"email": f"{role}@bakeflow.test",
                           "password": f"{role}-test-password"}, headers={"X-Requested-With": "BakeFlow"})
    assert response.status_code == 204, response.text
    return {"X-CSRF-Token": client.get("/api/session").json()["csrfToken"]}


def test_only_a_signed_in_owner_with_the_csrf_token_records_a_receipt(app, store, seeded):
    with TestClient(app) as client:
        assert client.get("/api/inventory/items").status_code == 401
        assert client.post("/api/inventory/receipts", json=body(seeded)).status_code in (401, 403)
        headers = login(client, "customer")
        assert client.get("/api/inventory/items").status_code == 403
        assert client.post("/api/inventory/receipts", json=body(seeded), headers=headers).status_code == 403
    with TestClient(app) as client:
        headers = login(client, "owner")
        assert client.post("/api/inventory/receipts", json=body(seeded)).status_code == 403
        assert client.post("/api/inventory/receipts", json=body(seeded), headers=headers).status_code == 201
        assert client.get("/api/inventory/items").status_code == 200
    assert on_hand(store, seeded.items["Flour"]) == (Decimal("3500.000"), 2)
    assert audits(store, RECEIPT_ACTION)[0].actor_id == seeded.owner_id
