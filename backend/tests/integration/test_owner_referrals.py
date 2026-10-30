"""BAKE-17: real database queue, versioned resolution, no premature sale or stock writes."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.llm.gateway import MockLLMClient
from app.main import create_app
from app.sales.intake import CustomerIdentity, IntakeError
from app.sales.referrals import ReferralIntake, ReferralService, Resolution
from app.sales.referral_fixtures import CUSTOM_MESSAGE, UNCERTAIN_MESSAGE
from app.shared.records import AuditEntry, Customer, InventoryItem, Order, OwnerReferral, Recipe, ShoppingList, Transaction
from app.shared.store import StaleVersionError


@pytest.fixture
def customer(seeded):
    return CustomerIdentity(seeded.customer_user_id, seeded.customer_id, "Customer")


@pytest.fixture
def service(store):
    return ReferralService(store)


def summary():
    return {"items": [{"product": "Vanilla cupcake", "quantity": 24, "customisation": "replace milk with oat milk"}],
            "required_at": (datetime.now(UTC) + timedelta(days=14)).isoformat(),
            "collection_kind": "before", "allergen_concerns": ["milk concern"], "allergen_statement": "milk concern"}


def create(service, customer):
    return service.create(customer, "24 cupcakes with a milk concern", summary=summary())


def resolution(store, seeded):
    with store.unit_of_work() as db:
        recipe = db.get(Recipe, seeded.recipes["Vanilla cupcake"])
        return Resolution(summary=summary(), recipes=[{"productId": recipe.product_id,
                          "recipeId": recipe.recipe_id, "recipeVersion": recipe.version}],
                          note="Recipe and concern discussed with customer; review details before confirming.")


def count(store, model):
    with store.unit_of_work() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_ingredient_request_pending_without_order_or_stock_check(store, customer):
    data = summary()
    intake = ReferralIntake(store, MockLLMClient(default_reply=json.dumps(data)), b"test")
    result = intake.preview(customer, "24 cupcakes with a milk concern")
    assert result["confirmationToken"] is None
    assert result["ownerReview"]["status"] == "Pending"
    assert count(store, OwnerReferral) == 1
    assert count(store, Order) == count(store, ShoppingList) == 0


def test_missing_allergen_information_is_not_assumed_clear(store, customer):
    data = summary()
    data["items"][0]["customisation"] = None
    data["allergen_concerns"] = data["allergen_statement"] = None
    result = ReferralIntake(store, MockLLMClient(default_reply=json.dumps(data)), b"test").preview(customer, "24 cupcakes")
    assert result["confirmationToken"] is None
    assert count(store, OwnerReferral) == 0


def test_owner_resolution_rechecked_then_customer_saves_once(store, service, customer, seeded):
    row = create(service, customer)
    resolved = service.resolve(uuid.UUID(row["id"]), row["version"], seeded.owner_id, resolution(store, seeded))
    assert resolved["status"] == "AwaitingCustomer" and count(store, Order) == 0
    key = uuid.UUID(row["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: service.confirm(key, resolved["version"], customer), range(2)))
    assert receipts[0]["orderId"] == receipts[1]["orderId"]
    assert count(store, Order) == 1
    with store.unit_of_work() as db:
        order = db.get(Order, uuid.UUID(receipts[0]["orderId"]))
        assert order.status == "Draft"
        assert order.items[0].resolved_recipe_id == seeded.recipes["Vanilla cupcake"]
        assert order.allergen_concerns == ["milk concern"]
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "order.reviewed_draft_created")) == 1
        assert db.scalar(select(InventoryItem.quantity_on_hand).where(InventoryItem.item_id == seeded.items["Flour"])) > 0


def test_recipe_revision_invalidates_confirmation(store, service, customer, seeded):
    row = create(service, customer)
    resolved = service.resolve(uuid.UUID(row["id"]), 1, seeded.owner_id, resolution(store, seeded))
    with store.unit_of_work() as db:
        recipe = db.get(Recipe, seeded.recipes["Vanilla cupcake"])
        recipe.yield_units += 1
        store.commit(db, recipe, actor_id=seeded.owner_id, action="test.recipe_revision", expected_version=recipe.version, revise=True)
    with pytest.raises(IntakeError, match="recipe"):
        service.confirm(uuid.UUID(row["id"]), resolved["version"], customer)
    assert count(store, Order) == 0


@pytest.mark.parametrize("invalid", ["past", "quantity", "unsupported", "missing_allergy", "wrong_recipe"])
def test_owner_resolution_must_validate(store, service, customer, seeded, invalid):
    row = create(service, customer)
    data = resolution(store, seeded)
    if invalid == "past":
        data.summary.required_at = datetime.now(UTC) - timedelta(days=1)
    elif invalid == "quantity":
        data.summary.items[0].quantity = 0
    elif invalid == "unsupported":
        data.summary.items[0].product = "unknown"
    elif invalid == "missing_allergy":
        data.summary.allergen_concerns = None
    else:
        data.recipes[0].recipeId = uuid.uuid4()
    with pytest.raises(IntakeError):
        service.resolve(uuid.UUID(row["id"]), 1, seeded.owner_id, data)
    assert count(store, Order) == 0


def test_clarification_and_correction_invalidate_old_resolution(store, service, customer, seeded):
    row = create(service, customer)
    key = uuid.UUID(row["id"])
    question = service.ask(key, 1, seeded.owner_id, "Which ingredient must be avoided?")
    answer = service.reply(key, question["version"], customer, "Milk")
    resolved = service.resolve(key, answer["version"], seeded.owner_id, resolution(store, seeded))
    corrected = service.reply(key, resolved["version"], customer, "Also eggs")
    assert corrected["status"] == "Pending" and corrected["resolution"] is None
    with pytest.raises(StaleVersionError):
        service.confirm(key, resolved["version"], customer)
    with pytest.raises(IntakeError):
        service.confirm(key, corrected["version"], customer)
    assert count(store, Order) == 0


def test_duplicates_create_one_review(store, service, customer):
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: create(service, customer), range(2)))
    assert rows[0]["id"] == rows[1]["id"]
    assert count(store, OwnerReferral) == 1


def test_failure_rolls_back_order_and_referral(store, service, customer, seeded, monkeypatch):
    row = create(service, customer)
    resolved = service.resolve(uuid.UUID(row["id"]), 1, seeded.owner_id, resolution(store, seeded))
    original = store.commit
    def fail(db, record, **kw):
        if kw.get("action") == "exception.customer_confirmed":
            raise RuntimeError("save failure")
        return original(db, record, **kw)
    monkeypatch.setattr(store, "commit", fail)
    with pytest.raises(RuntimeError):
        service.confirm(uuid.UUID(row["id"]), resolved["version"], customer)
    assert count(store, Order) == 0
    with store.unit_of_work() as db:
        assert db.get(OwnerReferral, uuid.UUID(row["id"])).status == "AwaitingCustomer"


@pytest.mark.parametrize("kind", ["Refund", "InProductionChange"])
def test_existing_order_review_does_not_execute_changes(store, service, customer, seeded, kind):
    with store.unit_of_work() as db:
        order = Order(customer_id=customer.customer_id, required_at=datetime.now(UTC) + timedelta(days=7), status="InProduction", allergen_concerns=[])
        store.commit(db, order, actor_id=customer.user_id, action="test.order")
        oid = order.order_id
        stock = list(db.scalars(select(InventoryItem.quantity_on_hand).order_by(InventoryItem.item_id)))
    row = service.create(customer, "Please review my request", kind=kind, order_id=oid)
    resolved = service.resolve(uuid.UUID(row["id"]), 1, seeded.owner_id, Resolution(note="Discuss revised commitments before applying any change."))
    service.confirm(uuid.UUID(row["id"]), resolved["version"], customer)
    with store.unit_of_work() as db:
        assert db.get(Order, oid).status == "InProduction"
        assert list(db.scalars(select(InventoryItem.quantity_on_hand).order_by(InventoryItem.item_id))) == stock
    assert count(store, Transaction) == 0


def test_http_auth_and_full_mock_review(database_url, seeded, store):
    app = create_app(Settings(_env_file=None, database_url=database_url, environment="test", app_secret="test-secret-" * 4))
    def login(client, role):
        assert client.post("/api/auth/login", json={"email": f"{role}@bakeflow.test", "password": f"{role}-test-password"}, headers={"X-Requested-With": "BakeFlow"}).status_code == 204
        return {"X-CSRF-Token": client.get("/api/session").json()["csrfToken"]}
    with TestClient(app) as client:
        assert client.get("/api/exception-requests").status_code == 401
        headers = login(client, "customer")
        assert client.get("/api/owner/exception-catalogue").status_code == 403
        result = client.post("/api/order-requests", json={"message": CUSTOM_MESSAGE}, headers=headers)
        assert result.status_code == 200, result.text
        row = result.json()["ownerReview"]
        assert client.post(f"/api/owner/exception-requests/{row['id']}/question", json={"version": 1, "text": "test"}, headers=headers).status_code == 403
        assert client.post(f"/api/exception-requests/{row['id']}/confirm", json={"version": 1}).status_code == 403
        assert client.post("/api/order-requests", json={"message": UNCERTAIN_MESSAGE}, headers=headers).json()["ownerReview"]["status"] == "Pending"
        headers = login(client, "owner")
        assert len(client.get("/api/exception-requests").json()["requests"]) == 2
        response = client.post(f"/api/owner/exception-requests/{row['id']}/resolve", json={"version": 1, "resolution": resolution(store, seeded).model_dump(mode="json")}, headers=headers)
        assert response.status_code == 200, response.text
        headers = login(client, "customer")
        saved = client.post(f"/api/exception-requests/{row['id']}/confirm", json={"version": response.json()["version"]}, headers=headers)
        assert saved.status_code == 200 and saved.json()["status"] == "Completed"


def test_other_customer_gets_same_not_found(store, service, customer, seeded):
    row = create(service, customer)
    stranger = CustomerIdentity(uuid.uuid4(), uuid.uuid4(), "Other")
    for key in (uuid.UUID(row["id"]), uuid.uuid4()):
        with pytest.raises(IntakeError, match="Request not found"):
            service.confirm(key, 1, stranger)
    with store.unit_of_work() as db:
        assert db.get(Customer, customer.customer_id) is not None
