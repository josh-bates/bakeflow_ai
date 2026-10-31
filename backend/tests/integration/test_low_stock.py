"""BAKE-37 [O1]: low-stock alerts and reorder suggestions, on PostgreSQL.

Jira tests: below, at and above the reorder level; commitments to released
plans lower available stock; alerts never change stock. Manual test, here
automated: after a large confirmed order is released, flour shows an alert
with a suggested quantity. The explanation is owner-requested, checked like
BAKE-31's, and the alerts stay usable when it fails.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.inventory_production import (
    InventoryService,
    LowStockExplainer,
    LowStockStatus,
    ReceiptInput,
    ReorderLevelRejected,
)
from app.inventory_production.fixtures import MockLowStockExplainer, explanation_reply
from app.inventory_production.inventory import REORDER_ACTION
from app.inventory_production.low_stock import CHANGED_NOTICE, PROMPT_VERSION, UNAVAILABLE_NOTICE
from app.llm.gateway import LLMCallFailed, MockLLMClient
from app.main import create_app
from app.shared import RecordNotFound, StaleVersionError
from app.shared.records import (
    AIRequest,
    AuditEntry,
    InventoryItem,
    Order,
    OrderItem,
    OrderStatus,
    ProductionSchedule,
    ProductionScheduleVersion,
    ProductionTask,
)

PLAN_DAY = date(2026, 11, 5)
DAY_START = datetime(2026, 11, 4, 19, 0, tzinfo=UTC)  # 06:00 on 5 Nov in Sydney
REQUIRED_AT = datetime(2026, 11, 5, 1, 0, tzinfo=UTC)


@pytest.fixture
def inventory(store) -> InventoryService:
    return InventoryService(store)


def released_order(store, seeded, flour_grams: int, *, release: bool = True):
    """A Confirmed order in a plan that commits ``flour_grams`` of the 1,000 g flour."""
    with store.unit_of_work() as session:
        order = Order(
            customer_id=seeded.customer_id,
            required_at=REQUIRED_AT,
            status=OrderStatus.CONFIRMED,
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=36)],
        )
        ref = store.commit(session, order, actor_id=seeded.owner_id, action="test.order")
        schedule = ProductionSchedule(created_by=seeded.owner_id)
        store.commit(session, schedule, actor_id=seeded.owner_id, action="test.schedule")
        store.commit(session, ProductionScheduleVersion(
            schedule_id=schedule.schedule_id, version=1, start_date=PLAN_DAY, end_date=PLAN_DAY,
            daily_capacity_minutes=240,
            requirements_snapshot={str(ref.record_id): {str(seeded.items["Flour"]): f"{flour_grams}.000"}},
            source_order_refs=[ref.to_json()],
        ), actor_id=seeded.owner_id, action="test.version")
        store.commit(session, ProductionTask(
            schedule_id=schedule.schedule_id, schedule_version=1, order_id=ref.record_id,
            task_kind="bake", start_at=DAY_START, end_at=DAY_START + timedelta(minutes=30),
        ), actor_id=seeded.owner_id, action="test.task")
        if release:
            schedule.released_version = 1
            store.commit(session, schedule, actor_id=seeded.owner_id, action="test.release",
                         expected_version=schedule.version)
    return ref


def flour(levels):
    return next(level for level in levels if level.ingredient == "Flour")


def evidence(store) -> tuple:
    """Everything an alert must leave alone: stock, levels, versions and the audit trail."""
    with store.unit_of_work() as session:
        items = sorted(
            (i.item_id, i.quantity_on_hand, i.reorder_threshold, i.version)
            for i in session.scalars(select(InventoryItem))
        )
        return items, session.scalar(select(func.count()).select_from(AuditEntry))


# --- the deterministic alert --------------------------------------------------


@pytest.mark.parametrize(
    ("committed", "low", "suggested"),
    [(900, True, Decimal("300.000")), (800, False, None), (700, False, None)],
    ids=["below", "at", "above"],
)
def test_flour_is_low_only_below_its_reorder_level(store, seeded, inventory, committed, low, suggested):
    released_order(store, seeded, committed)

    level = flour(inventory.stock_levels())

    assert level.reorder_level == Decimal("200.000")  # seeded
    assert level.available == Decimal(1000 - committed).quantize(Decimal("0.001"))
    assert (level.low, level.suggested_reorder) == (low, suggested)
    assert ("Flour" in [line.ingredient for line in inventory.low_stock()]) is low


def test_only_released_commitments_lower_available_stock(store, seeded, inventory):
    assert "Flour" not in [line.ingredient for line in inventory.low_stock()]

    released_order(store, seeded, 900, release=False)
    assert "Flour" not in [line.ingredient for line in inventory.low_stock()]

    released_order(store, seeded, 900)
    assert flour(inventory.low_stock()).available == Decimal("100.000")


def test_over_committed_stock_suggests_enough_to_cover_the_plans_too(store, seeded, inventory):
    released_order(store, seeded, 600)
    released_order(store, seeded, 500)

    level = flour(inventory.low_stock())

    assert level.available == Decimal("-100.000")
    # Back to twice the 200 g level: 400 g plus the 100 g the plans lack.
    assert level.suggested_reorder == Decimal("500.000")


def test_reading_alerts_never_changes_stock(store, seeded, inventory):
    released_order(store, seeded, 900)
    before = evidence(store)

    inventory.stock_levels()
    inventory.low_stock()

    assert evidence(store) == before


# --- the owner sets reorder levels --------------------------------------------


def test_setting_a_reorder_level_changes_only_that_level(store, seeded, inventory):
    item_id = seeded.items["Flour"]
    before = flour(inventory.stock_levels())

    saved = inventory.set_reorder_level(item_id, Decimal("950"), expected_version=before.version,
                                        actor_id=seeded.owner_id)

    assert (saved.reorder_level, saved.version) == (Decimal("950.000"), before.version + 1)
    assert (saved.on_hand, saved.low, saved.suggested_reorder) == (before.on_hand, False, None)
    with store.unit_of_work() as session:
        entry = session.scalars(select(AuditEntry).where(AuditEntry.action == REORDER_ACTION)).one()
    assert entry.before_data["reorder_threshold"] == "200.000"
    assert entry.after_data["reorder_threshold"] == "950.000"
    assert entry.after_data["quantity_on_hand"] == entry.before_data["quantity_on_hand"]
    # Raising the level above what is available makes it low.
    saved = inventory.set_reorder_level(item_id, Decimal("1000.5"), expected_version=saved.version,
                                        actor_id=seeded.owner_id)
    assert (saved.low, saved.suggested_reorder) == (True, Decimal("1001.000"))


def test_setting_the_same_level_again_changes_nothing(store, seeded, inventory):
    level = flour(inventory.stock_levels())
    before = evidence(store)

    again = inventory.set_reorder_level(seeded.items["Flour"], Decimal("200.0"),
                                        expected_version=level.version, actor_id=seeded.owner_id)

    assert again == level
    assert evidence(store) == before


def test_a_level_for_an_older_version_is_refused(store, seeded, inventory):
    level = flour(inventory.stock_levels())
    inventory.receive_stock(ReceiptInput(seeded.items["Flour"], Decimal("100"), "Invoice", "INV-37"),
                            actor_id=seeded.owner_id)
    before = evidence(store)

    with pytest.raises(StaleVersionError):
        inventory.set_reorder_level(seeded.items["Flour"], Decimal("300"), expected_version=level.version,
                                    actor_id=seeded.owner_id)

    assert evidence(store) == before


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (Decimal("-1"), "must be zero or more"),
        (Decimal("0.0005"), "must have at most 3 decimal places"),
        (Decimal("NaN"), "must be a finite number"),
        (Decimal("1E+9"), "must be at most 999999999.999"),
        ("200", "must be a number, such as 200 or 0.5"),
        (200.0, "must be a number, such as 200 or 0.5"),
    ],
)
def test_an_invalid_level_is_refused(store, seeded, inventory, value, message):
    before = evidence(store)

    with pytest.raises(ReorderLevelRejected, match=message):
        inventory.set_reorder_level(seeded.items["Flour"], value, expected_version=1, actor_id=seeded.owner_id)

    assert evidence(store) == before


def test_an_unknown_item_is_not_found(store, seeded, inventory):
    with pytest.raises(RecordNotFound):
        inventory.set_reorder_level(seeded.customer_id, Decimal("1"), expected_version=1,
                                    actor_id=seeded.owner_id)


# --- the owner-requested explanation ------------------------------------------


def test_the_explanation_names_each_low_ingredient_and_changes_nothing(store, seeded):
    released_order(store, seeded, 900)
    before = evidence(store)
    client = MockLowStockExplainer()

    outcome = LowStockExplainer(store, client).explain(actor_id=seeded.owner_id)

    assert outcome.status is LowStockStatus.EXPLAINED
    assert [line.ingredient for line in outcome.lines] == ["Flour"]
    assert "Suggested reorder: 300 g" in outcome.text
    assert evidence(store) == before
    request = json.loads(client.calls[0].rsplit("TaskRequest: ", 1)[1])
    assert request["promptVersion"] == PROMPT_VERSION
    assert request["inputRefs"] == [{"recordType": "InventoryItem", "recordId": str(seeded.items["Flour"]),
                                     "version": 1}]
    assert request["inputData"]["ingredients"] == [{
        "ingredient": "Flour", "unit": "g", "on_hand": "1000", "committed": "900", "available": "100",
        "reorder_level": "200", "suggested_reorder": "300",
    }]


def test_with_nothing_low_the_agent_is_not_called(store, seeded):
    client = MockLowStockExplainer()

    outcome = LowStockExplainer(store, client).explain(actor_id=seeded.owner_id)

    assert (outcome.status, outcome.lines, client.calls) == (LowStockStatus.NO_ALERTS, (), [])


@pytest.mark.parametrize(
    "reply",
    [
        explanation_reply("Flour is low: reorder 250 g."),  # not a figure on the list
        explanation_reply("Flour: 100 g available, below 200 g."),  # no suggested reorder
        explanation_reply("Flour and butter are low; reorder 300 g of flour."),  # butter is not low
        explanation_reply("Flour is short by -100 g. Reorder 300 g."),  # signed
        "not json",
    ],
    ids=["other-number", "no-suggestion", "other-ingredient", "signed", "unreadable"],
)
def test_an_unusable_explanation_leaves_the_alerts_usable(store, seeded, reply):
    released_order(store, seeded, 900)
    before = evidence(store)
    client = MockLLMClient(default_reply=reply)

    outcome = LowStockExplainer(store, client).explain(actor_id=seeded.owner_id)

    assert outcome.status is LowStockStatus.UNAVAILABLE
    assert (outcome.text, outcome.notice) == ("", UNAVAILABLE_NOTICE)
    assert flour(outcome.lines).suggested_reorder == Decimal("300.000")
    assert len(client.calls) == 3
    assert evidence(store) == before


def test_two_alerts_are_explained_but_never_with_their_suggestions_swapped(store, seeded, inventory):
    for name, level in [("Flour", "1200"), ("Caster sugar", "2500")]:
        inventory.set_reorder_level(seeded.items[name], Decimal(level), expected_version=1, actor_id=seeded.owner_id)
    assert {line.ingredient: line.suggested_reorder for line in inventory.low_stock()} == {
        "Caster sugar": Decimal("3000.000"), "Flour": Decimal("1400.000"),
    }
    swapped = MockLLMClient(default_reply=explanation_reply(
        "Flour: suggested reorder 3000 g. Caster sugar: suggested reorder 1400 g."
    ))

    assert LowStockExplainer(store, MockLowStockExplainer()).explain(
        actor_id=seeded.owner_id
    ).status is LowStockStatus.EXPLAINED
    assert LowStockExplainer(store, swapped).explain(actor_id=seeded.owner_id).status is LowStockStatus.UNAVAILABLE
    assert len(swapped.calls) == 3


def test_a_provider_failure_leaves_the_alerts_usable(store, seeded):
    released_order(store, seeded, 900)

    class Failing:
        def complete(self, prompt):
            raise LLMCallFailed("provider call failed: TimeoutError: timed out")

    outcome = LowStockExplainer(store, Failing()).explain(actor_id=seeded.owner_id)

    assert outcome.status is LowStockStatus.UNAVAILABLE
    assert [line.ingredient for line in outcome.lines] == ["Flour"]


def test_stock_received_while_the_agent_writes_returns_the_current_alerts(store, seeded):
    released_order(store, seeded, 900)
    mock = MockLowStockExplainer()

    class ReceiptMeanwhile:
        def complete(self, prompt):
            InventoryService(store).receive_stock(
                ReceiptInput(seeded.items["Flour"], Decimal("50"), "Invoice", "INV-38"),
                actor_id=seeded.owner_id,
            )
            return mock.complete(prompt)

    outcome = LowStockExplainer(store, ReceiptMeanwhile()).explain(actor_id=seeded.owner_id)

    assert (outcome.status, outcome.text, outcome.notice) == (LowStockStatus.CHANGED, "", CHANGED_NOTICE)
    assert flour(outcome.lines).available == Decimal("150.000")


# --- through the app ----------------------------------------------------------


def login(client, role="customer"):
    response = client.post(
        "/api/auth/login",
        json={"email": f"{role}@bakeflow.test", "password": f"{role}-test-password"},
        headers={"X-Requested-With": "BakeFlow"},
    )
    assert response.status_code == 204, response.text
    return {"X-CSRF-Token": client.get("/api/session").json()["csrfToken"]}


@pytest.fixture
def app(database_url, seeded):
    return create_app(Settings(_env_file=None, database_url=database_url, environment="test",
                               app_secret="test-secret-" * 4, llm_mode="mock"))


def level_path(seeded) -> str:
    return f"/api/inventory/items/{seeded.items['Flour']}/reorder-level"


def test_after_a_large_order_is_released_the_owner_sees_and_explains_a_flour_alert(app, store, seeded):
    released_order(store, seeded, 900)  # 36 vanilla cupcakes

    with TestClient(app) as client:
        headers = login(client, "owner")
        items = client.get("/api/inventory/items").json()["items"]
        explained = client.post("/api/inventory/low-stock/explanation", headers=headers)

    flour_json = next(item for item in items if item["ingredient"] == "Flour")
    assert flour_json | {"id": None} == {
        "id": None, "ingredient": "Flour", "unit": "g", "onHand": "1000.000", "committed": "900.000",
        "available": "100.000", "reorderLevel": "200.000", "version": 1, "low": True,
        "suggestedReorder": "300.000",
    }
    assert [item["ingredient"] for item in items if item["low"]] == ["Flour"]
    assert explained.status_code == 200, explained.text
    body = explained.json()
    assert (body["status"], body["notice"]) == ("Explained", None)
    assert "Suggested reorder: 300 g" in body["explanation"]
    assert [item["ingredient"] for item in body["items"]] == ["Flour"]
    with store.unit_of_work() as session:
        row = session.scalars(select(AIRequest)).one()
    assert (row.task_kind, row.status, row.prompt_version) == ("explainLowStock", "Succeeded", PROMPT_VERSION)


def test_the_owner_changes_a_reorder_level_through_the_api(app, store, seeded):
    with TestClient(app) as client:
        headers = login(client, "owner")
        saved = client.put(level_path(seeded), json={"reorderLevel": "1200", "version": 1}, headers=headers)
        stale = client.put(level_path(seeded), json={"reorderLevel": "300", "version": 1}, headers=headers)
        negative = client.put(level_path(seeded), json={"reorderLevel": "-1", "version": 2}, headers=headers)
        text = client.put(level_path(seeded), json={"reorderLevel": "lots", "version": 2}, headers=headers)
        unknown = client.put(f"/api/inventory/items/{seeded.customer_id}/reorder-level",
                             json={"reorderLevel": "1", "version": 1}, headers=headers)

    assert saved.status_code == 200, saved.text
    assert {k: saved.json()[k] for k in ("reorderLevel", "version", "low", "suggestedReorder")} == {
        "reorderLevel": "1200.000", "version": 2, "low": True, "suggestedReorder": "1400.000",
    }
    assert stale.status_code == 409 and "changed since you loaded it" in stale.json()["detail"]
    assert negative.status_code == 422
    assert negative.json()["issues"] == [{"field": "reorderLevel", "message": "must be zero or more"}]
    assert text.json()["issues"][0]["message"] == "must be a number, such as 200 or 0.5"
    assert unknown.status_code == 404


def test_a_customer_can_neither_change_levels_nor_ask_for_explanations(app, seeded):
    with TestClient(app) as client:
        headers = login(client)
        changed = client.put(level_path(seeded), json={"reorderLevel": "0", "version": 1}, headers=headers)
        explained = client.post("/api/inventory/low-stock/explanation", headers=headers)

    assert (changed.status_code, explained.status_code) == (403, 403)


def test_reading_stock_levels_never_calls_a_model(store, seeded, database_url):
    from fastapi import FastAPI
    from app.auth.access import Identity
    from app.inventory_production.routes import make_router

    released_order(store, seeded, 900)
    calls = []
    app = FastAPI()

    @app.middleware("http")
    async def signed_in(request, call_next):
        request.state.identity = Identity(seeded.owner_id, "Owner", "Owner", None)
        return await call_next(request)

    app.include_router(make_router(
        Settings(_env_file=None, database_url=database_url, llm_mode="live"), store=store,
        low_stock_client_factory=lambda: calls.append("model") or MockLowStockExplainer(),
    ))
    with TestClient(app) as client:
        assert client.get("/api/inventory/items").json()["items"]

    assert calls == []
