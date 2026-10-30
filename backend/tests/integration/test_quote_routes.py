"""BAKE-23 routes: the owner approves a quote and the customer accepts it, over HTTP.

The real app with BAKE-14 sign-in and CSRF, on PostgreSQL. The service rules
themselves are tested in ``test_quote_flow.py``; these tests check the routes,
their permissions and what each side is shown.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.inventory_production import InventoryService
from app.main import create_app
from app.shared.passwords import hash_password
from app.shared.records import Customer, Order, OrderItem, OrderStatus, UserAccount

REQUIRED_AT = datetime.now(UTC) + timedelta(days=14)


@pytest.fixture
def app(database_url, seeded):
    return create_app(Settings(_env_file=None, database_url=database_url,
                               environment="test", app_secret="test-secret-" * 4))


def login(client, role="customer"):
    response = client.post("/api/auth/login", json={"email": f"{role}@bakeflow.test",
                           "password": f"{role}-test-password"}, headers={"X-Requested-With": "BakeFlow"})
    assert response.status_code == 204, response.text
    return {"X-CSRF-Token": client.get("/api/session").json()["csrfToken"]}


def priced_order(store, seeded) -> str:
    """A customer's Draft order of 24 vanilla cupcakes with its stock check done."""
    with store.unit_of_work() as session:
        order = Order(
            customer_id=seeded.customer_id, required_at=REQUIRED_AT, allergen_concerns=[],
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=24,
                             customisation="standard")],
        )
        ref = store.commit(session, order, actor_id=seeded.customer_user_id, action="order.draft")
    InventoryService(store).run_stock_check(ref.record_id, expected_order_version=ref.version,
                                            actor_id=seeded.owner_id)
    return str(ref.record_id)


def queued(client, order_id) -> dict | None:
    return next((o for o in client.get("/api/finance/quotes").json()["orders"]
                 if o["orderId"] == order_id), None)


def status_of(store, order_id) -> str:
    with store.unit_of_work() as session:
        return store.load(session, Order, uuid.UUID(order_id)).status


def test_owner_approves_and_customer_accepts(app, store, seeded):
    order_id = priced_order(store, seeded)
    with TestClient(app) as client:
        owner = login(client, "owner")
        item = queued(client, order_id)
        assert (item["ready"], item["quote"], item["orderStatus"]) == (True, None, "Draft")

        drafted = client.post(f"/api/finance/orders/{order_id}/quotes", json={}, headers=owner)
        assert drafted.status_code == 201, drafted.text
        quote = drafted.json()
        # No explainer is wired in yet, so the quote is flagged for review (4a).
        assert (quote["total"], quote["version"]) == ("94.23", 1)
        assert (quote["explanation"], quote["requiresManualReview"]) == (None, True)
        assert queued(client, order_id)["approval"] == "Pending"
        base = f"/api/finance/quotes/{quote['id']}/versions/1"

        customer = login(client)
        assert client.get(f"/api/orders/{order_id}/quote").json() == {"quote": None}

        owner = login(client, "owner")
        assert client.post(f"{base}/approve", headers=owner).status_code == 200
        assert client.post(f"{base}/approve", headers=owner).status_code == 200  # a double click
        assert queued(client, order_id)["approval"] == "Approved"
        assert status_of(store, order_id) == OrderStatus.QUOTED

        customer = login(client)
        shown = client.get(f"/api/orders/{order_id}/quote").json()["quote"]
        # The customer sees the price to accept, not the bakery's costs.
        assert shown == {"id": quote["id"], "version": 1, "orderId": order_id,
                         "total": "94.23", "customerDecision": "Pending"}
        accept = f"/api/quotes/{quote['id']}/versions/1/accept"
        accepted = client.post(accept, headers=customer)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json() == {"orderId": order_id, "replayed": False}
        assert client.post(accept, headers=customer).json()["replayed"] is True
        assert status_of(store, order_id) == OrderStatus.CONFIRMED

        login(client, "owner")
        assert queued(client, order_id) is None  # confirmed orders leave the queue


def test_withhold_revise_with_an_extra_cost_then_reject(app, store, seeded):
    order_id = priced_order(store, seeded)
    with TestClient(app) as client:
        owner = login(client, "owner")
        quote = client.post(f"/api/finance/orders/{order_id}/quotes", headers=owner).json()
        v1 = f"/api/finance/quotes/{quote['id']}/versions/1"

        assert client.post(f"{v1}/withhold", headers=owner).status_code == 200
        assert queued(client, order_id)["approval"] == "Withheld"
        refused = client.post(f"{v1}/approve", headers=owner)
        assert refused.status_code == 409
        assert "revise" in refused.json()["detail"]

        revised = client.post(f"{v1}/revise", headers=owner, json={
            "additionalCosts": [{"description": "Gift box", "amount": "5.00"}]})
        assert revised.status_code == 201, revised.text
        assert (revised.json()["version"], revised.json()["additionalCost"]) == (2, "5.00")
        assert client.post(f"{v1}/approve", headers=owner).status_code == 409  # superseded
        v2 = f"/api/finance/quotes/{quote['id']}/versions/2"
        assert client.post(f"{v2}/approve", headers=owner).status_code == 200

        customer = login(client)
        shown = client.get(f"/api/orders/{order_id}/quote").json()["quote"]
        assert shown["version"] == 2
        rejected = client.post(f"/api/quotes/{quote['id']}/versions/2/reject", headers=customer)
        assert rejected.json()["customerDecision"] == "Rejected"

        login(client, "owner")
        assert queued(client, order_id)["quote"]["customerDecision"] == "Rejected"


def test_amounts_must_be_decimal_strings(app, store, seeded):
    order_id = priced_order(store, seeded)
    with TestClient(app) as client:
        owner = login(client, "owner")
        url = f"/api/finance/orders/{order_id}/quotes"
        for cost in ({"description": "Box", "amount": 5}, {"description": "Box", "amount": "five"},
                     {"description": "Box", "amount": "5.001"}):
            response = client.post(url, headers=owner, json={"additionalCosts": [cost]})
            assert response.status_code == 422, cost
        assert queued(client, order_id)["quote"] is None


def test_each_side_only_reaches_its_own_routes(app, store, seeded):
    order_id = priced_order(store, seeded)
    uid = uuid.uuid4()
    with store.unit_of_work() as db:
        for record in [UserAccount(user_id=uid, email="other@bakeflow.test",
                                   password_hash=hash_password("other-test-password"), role="Customer"),
                       Customer(customer_id=uuid.uuid4(), user_id=uid, name="Other", contact="test")]:
            store.commit(db, record, actor_id=seeded.owner_id, action="test.seed")
    with TestClient(app) as client:
        assert client.get("/api/finance/quotes").status_code == 401
        owner = login(client, "owner")
        assert client.post(f"/api/finance/orders/{order_id}/quotes").status_code == 403  # no CSRF
        quote = client.post(f"/api/finance/orders/{order_id}/quotes", headers=owner).json()
        client.post(f"/api/finance/quotes/{quote['id']}/versions/1/approve", headers=owner)
        assert client.get(f"/api/orders/{order_id}/quote").status_code == 403  # owner is not the customer

        customer = login(client)
        assert client.get("/api/finance/quotes").status_code == 403
        assert client.post(f"/api/finance/quotes/{quote['id']}/versions/1/approve",
                           headers=customer).status_code == 403

        other = login(client, "other")
        assert client.get(f"/api/orders/{order_id}/quote").status_code == 404
        assert client.post(f"/api/quotes/{quote['id']}/versions/1/accept",
                           headers=other).status_code == 404
    assert status_of(store, order_id) == OrderStatus.QUOTED
