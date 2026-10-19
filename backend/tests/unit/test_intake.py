"""UC-01 extraction and persistence tests; SQLite checks do not prove PostgreSQL locking."""
import json
import uuid
from datetime import datetime, UTC, timedelta

import pytest
from sqlalchemy import create_engine, select, func, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.base import Base
from app.config import Settings
from app.llm.gateway import MockLLMClient
from app.sales.intake import OrderIntake, CustomerIdentity, IntakeError
from app.sales.routes import make_router, current_customer, check_csrf
from app.shared.records import Customer, UserAccount, Product, Order, AuditEntry, InventoryItem
from app.shared.store import ValidatedRecordStore
from app.workflow.order_flow import AIOperationFailed


@compiles(JSONB, "sqlite")
def sqlite_json(element, compiler, **kw):
    return "JSON"


def valid_data():
    return {"items": [{"product": "Vanilla cupcake", "quantity": 24, "flavour": "vanilla", "theme": "birthday"}],
            "required_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "collection_kind": "before", "allergen_concerns": [], "allergen_statement": "no allergy concerns", "budget": "100.00"}


@pytest.fixture
def environment():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    store = ValidatedRecordStore(sessionmaker(engine, expire_on_commit=False))
    uid, cid = uuid.uuid4(), uuid.uuid4()
    with store.unit_of_work() as db:
        records = [UserAccount(user_id=uid, email="customer@example.test", password_hash="test-only", role="Customer"),
                   Customer(customer_id=cid, user_id=uid, name="Customer", contact="demo"),
                   Product(name="Vanilla cupcake", category="cupcake", labour_minutes=1, packaging_cost=1),
                   InventoryItem(ingredient_name="Flour", unit="g", quantity_on_hand=1000, unit_cost=1)]
        for record in records:
            store.commit(db, record, actor_id=uid, action="test.seed")
    yield store, CustomerIdentity(uid, cid, "Customer")
    engine.dispose()


def service(environment, data=None):
    store, customer = environment
    client = MockLLMClient(default_reply=json.dumps(data if data is not None else valid_data()))
    return OrderIntake(store, client, b"test-summary-key"), customer, client


@pytest.mark.parametrize("change,expected", [
    ({"required_at": None}, "collection date"),
    ({"items": [{"product": "croissant", "quantity": 24}]}, "Unsupported"),
    ({"allergen_concerns": None}, "allergen"),
    ({"items": [{"product": "Vanilla cupcake", "quantity": 0}]}, "whole number"),
    ({"required_at": "2020-10-02T12:00:00+10:00"}, "future"),
    ({"budget": "-1.00"}, "budget"),
])
def test_incomplete_or_invalid_request_cannot_be_saved(environment, change, expected):
    data = valid_data() | change
    flow, customer, client = service(environment, data)
    preview = flow.preview(customer, "24 vanilla cupcakes, no allergy concerns")
    assert preview["confirmationToken"] is None
    assert expected in " ".join(preview["issues"])
    assert len(client.calls) == 1
    with flow.store.unit_of_work() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0


@pytest.mark.parametrize("data", [valid_data() | {"allergen_concerns": ["peanut allergy"]},
    valid_data() | {"items": [{"product": "Vanilla cupcake", "quantity": 24, "customisation": "replace eggs"}]}])
def test_owner_exceptions_cannot_be_saved(environment, data):
    flow, customer, _ = service(environment, data)
    result = flow.preview(customer, "24 vanilla cupcakes, no allergy concerns")
    assert result["confirmationToken"] is None
    assert "owner review" in " ".join(result["issues"])


def test_preview_then_confirmation_saves_one_draft_and_audit_without_stock_changes(environment):
    flow, customer, _ = service(environment)
    preview = flow.preview(customer, "24 vanilla cupcakes, no allergy concerns")
    assert preview["issues"] == []
    with flow.store.unit_of_work() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0
    receipt = flow.confirm(customer, preview["confirmationToken"])
    assert receipt["status"] == "Draft"
    assert flow.confirm(customer, preview["confirmationToken"]) == receipt
    with flow.store.unit_of_work() as db:
        order = db.scalars(select(Order)).one()
        assert order.items[0].quantity == 24
        assert order.items[0].theme == "birthday"
        assert order.items[0].resolved_recipe_id is None
        assert order.allergen_concerns == []
        assert order.collection_kind == "before"
        audit = db.scalars(select(AuditEntry).where(AuditEntry.action == "order.draft_created")).one()
        assert audit.action == "order.draft_created"
        assert audit.after_data["items"][0]["quantity"] == 24
        assert audit.after_data["allergen_concerns"] == []
        assert db.scalars(select(InventoryItem)).one().quantity_on_hand == 1000


def test_ai_failure_stops_at_three_calls_and_writes_nothing(environment):
    flow, customer, client = service(environment)
    client.default_reply = "malformed"
    with pytest.raises(AIOperationFailed):
        flow.preview(customer, "24 vanilla cupcakes, no allergy concerns")
    assert len(client.calls) == 3
    with flow.store.unit_of_work() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0


def test_recovers_from_invalid_output_and_rejects_boolean_quantity(environment):
    flow, customer, client = service(environment)
    client.replies = [json.dumps(valid_data() | {"items": [{"product": "Vanilla cupcake", "quantity": True}]}), "bad"]
    assert flow.preview(customer, "24 vanilla cupcakes, no allergy concerns")["confirmationToken"]
    assert len(client.calls) == 3


def test_tampered_or_cross_customer_token_is_rejected(environment):
    flow, customer, _ = service(environment)
    token = flow.preview(customer, "24 vanilla cupcakes, no allergy concerns")["confirmationToken"]
    for identity, value in [(customer, token + "x"), (CustomerIdentity(uuid.uuid4(), uuid.uuid4(), "other"), token)]:
        with pytest.raises(IntakeError):
            flow.confirm(identity, value)


def test_expired_token_is_rejected(environment, monkeypatch):
    flow, customer, _ = service(environment)
    token = flow.preview(customer, "24 vanilla cupcakes, no allergy concerns")["confirmationToken"]
    monkeypatch.setattr("app.sales.intake.time.time", lambda: 9999999999)
    with pytest.raises(IntakeError):
        flow.confirm(customer, token)


def test_save_failure_rolls_back_order_and_audit(environment, monkeypatch):
    flow, customer, _ = service(environment)
    token = flow.preview(customer, "24 vanilla cupcakes, no allergy concerns")["confirmationToken"]
    original = flow.store.commit
    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("database write failed")
    monkeypatch.setattr(flow.store, "commit", failed)
    with pytest.raises(RuntimeError):
        flow.confirm(customer, token)
    with flow.store.unit_of_work() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "order.draft_created")) == 0


def make_client(environment):
    store, identity = environment
    app = FastAPI()
    app.include_router(make_router(Settings(_env_file=None), store=store,
                                   client_factory=lambda _: MockLLMClient(default_reply=json.dumps(valid_data()))))
    return app, TestClient(app), identity


def test_http_requires_identity_and_csrf(environment):
    app, client, identity = make_client(environment)
    assert client.get("/api/orders/mine").status_code == 401
    app.dependency_overrides[current_customer] = lambda: identity
    assert client.post("/api/order-requests", json={"message": "24 vanilla cupcakes, no allergy concerns"}).status_code == 403


def test_http_two_steps_and_own_orders(environment):
    app, client, identity = make_client(environment)
    app.dependency_overrides[current_customer] = lambda: identity
    app.dependency_overrides[check_csrf] = lambda: None
    preview = client.post("/api/order-requests", json={"message": "24 vanilla cupcakes, no allergy concerns"})
    assert preview.status_code == 200
    assert client.get("/api/orders/mine").json() == {"orders": []}
    saved = client.post("/api/order-requests/confirm", json={"confirmationToken": preview.json()["confirmationToken"]})
    assert saved.status_code == 200
    assert saved.json()["status"] == "Draft"
    assert len(client.get("/api/orders/mine").json()["orders"]) == 1
    app.dependency_overrides[current_customer] = lambda: CustomerIdentity(uuid.uuid4(), uuid.uuid4(), "Other")
    assert client.get("/api/orders/mine").json() == {"orders": []}


def test_http_save_failure_is_not_success(environment, monkeypatch):
    from sqlalchemy.exc import OperationalError
    app, client, identity = make_client(environment)
    app.dependency_overrides[current_customer] = lambda: identity
    app.dependency_overrides[check_csrf] = lambda: None
    preview = client.post("/api/order-requests", json={"message": "24 vanilla cupcakes, no allergy concerns"}).json()
    def failed(*args, **kwargs):
        raise OperationalError("write", {}, Exception("private DB details"))
    monkeypatch.setattr(environment[0], "commit", failed)
    response = client.post("/api/order-requests/confirm", json={"confirmationToken": preview["confirmationToken"]})
    assert response.status_code == 503
    assert "private DB" not in response.text
    assert "orderId" not in response.json()


def test_hallucinated_no_allergies_cannot_complete_missing_disclosure(environment):
    flow, customer, _ = service(environment)
    result = flow.preview(customer, "24 vanilla birthday cupcakes tomorrow at noon")
    assert result["summary"]["allergen_concerns"] is None
    assert result["confirmationToken"] is None


def test_unrelated_quote_does_not_establish_no_allergies(environment):
    data = valid_data() | {"allergen_statement": "24 vanilla cupcakes"}
    flow, customer, _ = service(environment, data)
    assert flow.preview(customer, "24 vanilla cupcakes")["confirmationToken"] is None


@pytest.mark.parametrize("customisation", [None, "", "standard", " Standard ", "STANDARD"])
def test_standard_items_save_without_a_resolved_recipe(environment, customisation):
    data = valid_data()
    data["items"][0]["customisation"] = customisation
    flow, customer, _ = service(environment, data)
    preview = flow.preview(customer, "24 vanilla cupcakes, no allergy concerns")
    assert preview["issues"] == []
    assert preview["summary"]["items"][0]["customisation"] is None
    flow.confirm(customer, preview["confirmationToken"])
    with flow.store.unit_of_work() as db:
        item = db.scalars(select(Order)).one().items[0]
        assert item.customisation is None
        assert item.resolved_recipe_id is None
        audit = db.scalars(select(AuditEntry).where(AuditEntry.action == "order.draft_created")).one()
        assert audit.after_data["items"][0]["customisation"] is None


@pytest.mark.parametrize("customisation", ["standard, no eggs", "non-standard", "replace eggs"])
def test_nonstandard_customisation_still_requires_owner_review(environment, customisation):
    data = valid_data()
    data["items"][0]["customisation"] = customisation
    flow, customer, _ = service(environment, data)
    preview = flow.preview(customer, "24 vanilla cupcakes, no allergy concerns")
    assert preview["confirmationToken"] is None
    assert "owner review" in " ".join(preview["issues"])


def test_standard_marker_never_bypasses_allergen_review(environment):
    data = valid_data() | {"allergen_concerns": ["peanut allergy"], "allergen_statement": "peanut allergy"}
    data["items"][0]["customisation"] = "standard"
    flow, customer, _ = service(environment, data)
    preview = flow.preview(customer, "24 vanilla cupcakes, peanut allergy")
    assert preview["confirmationToken"] is None
    assert "owner review" in " ".join(preview["issues"])
