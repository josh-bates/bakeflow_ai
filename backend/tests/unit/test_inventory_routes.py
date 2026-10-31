"""BAKE-32, BAKE-37: stock, receipts and reorder levels are owner-only; quantities are decimal strings."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.access import Identity, owner_path
from app.config import Settings
from app.inventory_production.routes import make_router

ITEMS = "/api/inventory/items"
RECEIPTS = "/api/inventory/receipts"
REORDER = f"/api/inventory/items/{uuid.uuid4()}/reorder-level"
EXPLAIN = "/api/inventory/low-stock/explanation"


def client_as(role: str | None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def signed_in(request, call_next):
        # Stands in for BAKE-14's session middleware.
        if role is not None:
            request.state.identity = Identity(uuid.uuid4(), role, role, None)
        return await call_next(request)

    app.include_router(make_router(Settings(_env_file=None)))
    return TestClient(app)


def receipt(**changes) -> dict:
    return {"itemId": str(uuid.uuid4()), "quantity": "2500.000", "sourceType": "Invoice",
            "sourceReference": "INV-1001"} | changes


def test_routes_are_in_the_owner_only_family():
    # BAKE-14's middleware refuses customers and checks CSRF on these paths.
    assert owner_path(ITEMS) and owner_path(RECEIPTS)
    assert owner_path(REORDER) and owner_path(EXPLAIN)


def test_no_session_is_refused():
    client = client_as(None)
    assert client.get(ITEMS).status_code == 401
    assert client.post(RECEIPTS, json=receipt()).status_code == 401
    assert client.put(REORDER, json={"reorderLevel": "200", "version": 1}).status_code == 401
    assert client.post(EXPLAIN).status_code == 401


def test_a_customer_is_refused():
    client = client_as("Customer")
    assert client.get(ITEMS).status_code == 403
    assert client.post(RECEIPTS, json=receipt()).status_code == 403
    assert client.put(REORDER, json={"reorderLevel": "200", "version": 1}).status_code == 403
    assert client.post(EXPLAIN).status_code == 403


@pytest.mark.parametrize("quantity", [2500, 2500.5])
def test_a_json_number_quantity_is_refused(quantity):
    assert client_as("Owner").post(RECEIPTS, json=receipt(quantity=quantity)).status_code == 422


def test_unknown_fields_are_refused():
    response = client_as("Owner").post(RECEIPTS, json=receipt(onHand="99999.000"))
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"reorderLevel": 200, "version": 1},  # a JSON number, not a decimal string
        {"reorderLevel": "200"},  # no version seen
        {"reorderLevel": "200", "version": 0},
        {"reorderLevel": "200", "version": 1, "onHand": "5000.000"},  # stock is never set here
    ],
)
def test_a_reorder_level_body_must_be_exact(body):
    assert client_as("Owner").put(REORDER, json=body).status_code == 422
