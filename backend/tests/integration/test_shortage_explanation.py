"""BAKE-31: the owner asks the AI to explain a saved shopping list (UC-03 6, 6a).

Jira tests with a fake AI: the explanation is shown, and after the retries all
fail the numeric list is still there, unchanged. Also: no shortage needs no
AI call, a list for an older order version is refused, and the owner-only
endpoint returns the list with the unavailable notice when the AI fails.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.inventory_production import (
    ExplanationStatus,
    InventoryService,
    ShortageExplainer,
)
from app.inventory_production.fixtures import MockShortageExplainer, explanation_reply
from app.inventory_production.shortages import UNAVAILABLE_NOTICE
from app.llm.gateway import LLMCallFailed, MockLLMClient
from app.main import create_app
from app.shared import StaleVersionError
from app.shared.records import InventoryItem, Order, OrderItem, ShoppingList

GOOD = "Flour is short by 200 g; the order needs 1200 g."


def checked(store, seeded, cupcakes):
    """A Vanilla cupcake order and its saved list: 48 are 200 g short of flour, 24 are covered."""
    with store.unit_of_work() as session:
        order = Order(
            customer_id=seeded.customer_id,
            required_at=datetime(2026, 10, 2, 1, 0, tzinfo=UTC),
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=cupcakes)],
        )
        ref = store.commit(session, order, actor_id=seeded.customer_user_id, action="order.draft")
    return InventoryService(store).run_stock_check(
        ref.record_id, expected_order_version=1, actor_id=seeded.owner_id
    )


def login(client, role="customer"):
    response = client.post(
        "/api/auth/login",
        json={"email": f"{role}@bakeflow.test", "password": f"{role}-test-password"},
        headers={"X-Requested-With": "BakeFlow"},
    )
    assert response.status_code == 204, response.text
    return {"X-CSRF-Token": client.get("/api/session").json()["csrfToken"]}


def saved_lists(store):
    with store.unit_of_work() as session:
        return [(s.list_id, s.requirements, s.shortages) for s in session.scalars(select(ShoppingList))]


def stock(store):
    with store.unit_of_work() as session:
        return {i.item_id: (i.quantity_on_hand, i.version) for i in session.scalars(select(InventoryItem))}


def edit_order(store, seeded, order_id):
    with store.unit_of_work() as session:
        order = store.load(session, Order, order_id)
        order.items[0].quantity = 36
        store.commit(
            session, order, actor_id=seeded.owner_id, action="order.edit", expected_version=1, revise=True
        )


def test_explanation_is_shown_with_the_unchanged_list(store, seeded):
    result = checked(store, seeded, 48)
    client = MockShortageExplainer()

    outcome = ShortageExplainer(store, client).explain(result.shopping_list_id, actor_id=seeded.owner_id)

    assert outcome.status is ExplanationStatus.EXPLAINED
    assert "Flour: short by 200 g (the order needs 1200 g)." in outcome.text
    assert {line.item_id: line.required for line in outcome.lines} == result.requirements
    assert {line.item_id: line.short_by for line in outcome.lines if line.short_by} == result.shortages
    assert outcome.lines[0].ingredient == "Flour"  # shortages first
    assert outcome.order_ref == result.order_ref and len(client.calls) == 1
    request = json.loads(client.calls[0].rsplit("TaskRequest: ", 1)[1])
    assert request["subjectRef"]["recordId"] == str(result.shopping_list_id)
    assert request["inputRefs"] == [result.order_ref.to_json()]


def test_failed_retries_keep_the_numeric_list(store, seeded):
    result = checked(store, seeded, 48)
    before = (saved_lists(store), stock(store))
    client = MockLLMClient([
        explanation_reply("Flour is short by 150 g."),  # invented number
        "not json",
        explanation_reply(f"{GOOD} Also buy cocoa powder."),  # not on the list
        explanation_reply(GOOD),  # never reached: the budget is three calls
    ])
    explainer = ShortageExplainer(store, client)

    outcome = explainer.explain(result.shopping_list_id, actor_id=seeded.owner_id)

    assert outcome.status is ExplanationStatus.UNAVAILABLE
    assert outcome.notice == UNAVAILABLE_NOTICE and outcome.text == ""
    assert {line.item_id: line.required for line in outcome.lines} == result.requirements
    assert {line.item_id: line.short_by for line in outcome.lines if line.short_by} == result.shortages
    assert len(client.calls) == 3
    assert (saved_lists(store), stock(store)) == before
    assert [e.event_type for e in explainer.audit_log] == ["explainShortagesFailed"]
    assert explainer.audit_log[0].subject == outcome.shopping_list_ref


def test_a_retry_can_still_give_a_usable_explanation(store, seeded):
    result = checked(store, seeded, 48)
    client = MockLLMClient([explanation_reply("Flour is short by -200 g."), explanation_reply(GOOD)])

    outcome = ShortageExplainer(store, client).explain(result.shopping_list_id, actor_id=seeded.owner_id)

    assert outcome.status is ExplanationStatus.EXPLAINED and outcome.text == GOOD
    assert len(client.calls) == 2


def test_no_shortage_needs_no_explanation(store, seeded):
    result = checked(store, seeded, 24)
    client = MockShortageExplainer()

    outcome = ShortageExplainer(store, client).explain(result.shopping_list_id, actor_id=seeded.owner_id)

    assert outcome.status is ExplanationStatus.NO_SHORTAGES
    assert client.calls == [] and len(outcome.lines) == len(result.requirements)


def test_a_list_for_an_older_order_version_is_refused(store, seeded):
    result = checked(store, seeded, 48)
    edit_order(store, seeded, result.order_ref.record_id)
    client = MockShortageExplainer()

    with pytest.raises(StaleVersionError):
        ShortageExplainer(store, client).explain(result.shopping_list_id, actor_id=seeded.owner_id)
    assert client.calls == []


def test_an_order_edited_while_the_agent_ran_is_refused(store, seeded):
    result = checked(store, seeded, 48)

    class EditingClient:
        def complete(self, prompt: str) -> str:
            edit_order(store, seeded, result.order_ref.record_id)
            return explanation_reply(GOOD)

    with pytest.raises(StaleVersionError):
        ShortageExplainer(store, EditingClient()).explain(result.shopping_list_id, actor_id=seeded.owner_id)


def test_an_order_edited_while_the_agent_failed_is_refused(store, seeded):
    result = checked(store, seeded, 48)
    client = MockLLMClient()

    def edit_then_fail(prompt: str) -> str:
        if not client.calls:
            edit_order(store, seeded, result.order_ref.record_id)
        client.calls.append(prompt)
        raise LLMCallFailed("provider unavailable")

    client.complete = edit_then_fail

    with pytest.raises(StaleVersionError):
        ShortageExplainer(store, client).explain(result.shopping_list_id, actor_id=seeded.owner_id)
    assert len(client.calls) == 3


@pytest.fixture
def app_settings(database_url, seeded):
    return dict(_env_file=None, database_url=database_url, environment="test", app_secret="test-secret-" * 4)


def explain_path(list_id) -> str:
    return f"/api/inventory/shopping-lists/{list_id}/explanation"


def test_owner_endpoint_returns_the_list_and_its_explanation(store, seeded, app_settings):
    result = checked(store, seeded, 48)

    with TestClient(create_app(Settings(**app_settings, llm_mode="mock"))) as client:
        response = client.post(explain_path(result.shopping_list_id), headers=login(client, "owner"))
        missing = client.post(explain_path(seeded.owner_id), headers=login(client, "owner"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "Explained" and body["notice"] is None
    assert "short by 200 g" in body["explanation"]
    flour = next(line for line in body["lines"] if line["ingredient"] == "Flour")
    assert (flour["required"], flour["shortBy"]) == ("1200.000", "200.000")
    assert missing.status_code == 404


def test_customer_cannot_ask_for_an_explanation(store, seeded, app_settings):
    result = checked(store, seeded, 48)

    with TestClient(create_app(Settings(**app_settings))) as client:
        response = client.post(explain_path(result.shopping_list_id), headers=login(client))

    assert response.status_code == 403


@pytest.mark.parametrize("base_url", [None, "https://["], ids=["missing", "unreadable"])
def test_endpoint_shows_the_list_with_a_notice_when_the_ai_is_unavailable(
    store, seeded, app_settings, monkeypatch, base_url
):
    result = checked(store, seeded, 48)
    for name in ("BAKEFLOW_LLM_BASE_URL", "BAKEFLOW_LLM_API_KEY", "BAKEFLOW_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    if base_url:
        monkeypatch.setenv("BAKEFLOW_LLM_BASE_URL", base_url)
        monkeypatch.setenv("BAKEFLOW_LLM_API_KEY", "test-key")
        monkeypatch.setenv("BAKEFLOW_LLM_MODEL", "test-model")
    with store.unit_of_work() as session:
        lists = session.scalar(select(func.count()).select_from(ShoppingList))

    with TestClient(create_app(Settings(**app_settings, llm_mode="live"))) as client:
        response = client.post(explain_path(result.shopping_list_id), headers=login(client, "owner"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "Unavailable" and body["explanation"] is None
    assert body["notice"] == UNAVAILABLE_NOTICE
    assert {line["itemId"]: Decimal(line["required"]) for line in body["lines"]} == {
        str(item_id): quantity for item_id, quantity in result.requirements.items()
    }
    with store.unit_of_work() as session:
        assert session.scalar(select(func.count()).select_from(ShoppingList)) == lists
