"""BAKE-14: real cookie sign-in, PostgreSQL sessions and permission boundary."""
from datetime import UTC, datetime, timedelta
import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.access import require_owner
from app.auth.models import AuthSession
from app.auth.service import COOKIE_NAME, digest
from app.config import Settings
from app.db.session import get_sessionmaker
from app.main import create_app
from app.shared.passwords import hash_password
from app.shared.records import Customer, UserAccount, Order, OrderItem


@pytest.fixture
def app(database_url, seeded):
    return create_app(Settings(_env_file=None, database_url=database_url,
                              environment="test", app_secret="test-secret-" * 4))


def login(client, role="customer"):
    response = client.post("/api/auth/login", json={"email": f"{role}@bakeflow.test",
                           "password": f"{role}-test-password"}, headers={"X-Requested-With": "BakeFlow"})
    assert response.status_code == 204, response.text
    return {"X-CSRF-Token": client.get("/api/session").json()["csrfToken"]}


def test_login_logout_persisted_and_revoked(app, database_url):
    with TestClient(app) as client:
        assert client.get("/api/session").status_code == 401
        headers = login(client)
        token = client.cookies[COOKIE_NAME]
        with get_sessionmaker(database_url)() as db:
            assert db.get(AuthSession, digest(token)) is not None
            assert db.get(AuthSession, token) is None
        other_app = create_app(Settings(_env_file=None, database_url=database_url, environment="test", app_secret="test-secret-" * 4))
        with TestClient(other_app) as second:
            second.cookies.set(COOKIE_NAME, token)
            assert second.get("/api/session").status_code == 200
            assert client.post("/api/auth/logout").status_code == 403
            assert client.post("/api/auth/logout", headers=headers).status_code == 204
            assert second.get("/api/session").status_code == 401


def test_login_failures_csrf_cookie_and_validation(app):
    with TestClient(app) as client:
        def attempt(email, password="wrong", **kwargs):
            return client.post("/api/auth/login", json={"email": email, "password": password}, **kwargs)
        headers = {"X-Requested-With": "BakeFlow"}
        assert attempt("customer@bakeflow.test").status_code == 403
        assert attempt("customer@bakeflow.test", headers=headers).json() == attempt("missing@bakeflow.test", headers=headers).json()
        assert attempt("customer@bakeflow.test", headers=headers | {"Origin": "https://evil.test"}).status_code == 403
        secret = "private-password-marker"
        response = client.post("/api/auth/login", json={"email": [], "password": secret}, headers=headers)
        assert response.status_code == 422 and secret not in response.text
        response = attempt("customer@bakeflow.test", "customer-test-password", headers=headers)
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
        old = client.cookies[COOKIE_NAME]
        login(client)
        assert old != client.cookies[COOKIE_NAME]
        client.cookies.clear()
        client.cookies.set(COOKIE_NAME, old)
        assert client.get("/api/session").status_code == 401


@pytest.mark.parametrize("path", ["/api/approvals/test", "/api/inventory/test", "/api/finance/test", "/api/marketing/test", "/api/orders/test/approve"])
def test_owner_only_handler_never_runs_for_customer(app, path):
    calls = []
    # BAKE-15 implements approval writes; this probe verifies its shared guard.
    @app.post(path, dependencies=[Depends(require_owner)])
    def protected():
        calls.append(True)
        return {"allowed": True}
    with TestClient(app) as client:
        assert client.post(path).status_code == 401
        headers = login(client)
        assert client.post(path, headers=headers).status_code == 403
        assert not calls
        headers = login(client, "owner")
        assert client.post(path, headers=headers).status_code == 200
        assert calls == [True]


def test_customers_only_read_own_orders_owner_can_read_both(app, store, seeded):
    uid, cid = uuid.uuid4(), uuid.uuid4()
    with store.unit_of_work() as db:
        for record in [UserAccount(user_id=uid, email="other@bakeflow.test", password_hash=hash_password("other-test-password"), role="Customer"),
                       Customer(customer_id=cid, user_id=uid, name="Other", contact="test")]:
            store.commit(db, record, actor_id=seeded.owner_id, action="test.seed")
        ids = []
        for customer in [seeded.customer_id, cid]:
            record = Order(customer_id=customer, required_at=datetime.now(UTC) + timedelta(days=30),
                           items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=24, customisation="standard")])
            ref = store.commit(db, record, actor_id=seeded.owner_id, action="order.draft")
            ids.append(str(ref.record_id))
    with TestClient(app) as client:
        login(client)
        assert client.get(f"/api/orders/{ids[0]}").status_code == 200
        denied = client.get(f"/api/orders/{ids[1]}")
        missing = client.get(f"/api/orders/{uuid.uuid4()}")
        assert denied.status_code == missing.status_code == 404
        assert denied.json() == missing.json()
        assert [o["id"] for o in client.get("/api/orders/mine").json()["orders"]] == ids[:1]
        login(client, "other")
        assert client.get(f"/api/orders/{ids[0]}").status_code == 404
        assert client.get(f"/api/orders/{ids[1]}").status_code == 200
        login(client, "owner")
        assert all(client.get(f"/api/orders/{key}").status_code == 200 for key in ids)


def test_expired_cookie_rejected(app, database_url):
    with TestClient(app) as client:
        login(client)
        with get_sessionmaker(database_url).begin() as db:
            db.scalar(select(AuthSession)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert client.get("/api/session").status_code == 401


def test_production_cookie_is_secure(database_url, seeded):
    app = create_app(Settings(_env_file=None, database_url=database_url,
                             environment="production", app_secret="test-secret-" * 4))
    with TestClient(app, base_url="https://bakery.test") as client:
        response = client.post("/api/auth/login", json={"email": "owner@bakeflow.test", "password": "owner-test-password"}, headers={"X-Requested-With": "BakeFlow"})
        assert response.status_code == 204
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/session").json()["user"]["role"] == "Owner"


def test_migration_matches_models(database_url):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    backend = Path(__file__).resolve().parents[2]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    config.attributes["database_url"] = database_url
    command.check(config)


def test_signed_in_customer_can_save_draft_with_real_csrf(app):
    from app.sales.fixtures import DEMO_MESSAGE
    with TestClient(app) as client:
        headers = login(client)
        assert client.post("/api/order-requests", json={"message": DEMO_MESSAGE}).status_code == 403
        preview = client.post("/api/order-requests", json={"message": DEMO_MESSAGE}, headers=headers)
        assert preview.status_code == 200
        token = preview.json()["confirmationToken"]
        assert token
        receipt = client.post("/api/order-requests/confirm", json={"confirmationToken": token}, headers=headers)
        assert receipt.status_code == 200
        assert receipt.json()["status"] == "Draft"
        assert client.get("/api/orders/mine").json()["orders"][0]["id"] == receipt.json()["orderId"]


def test_authenticated_clarification_flow(app):
    from app.sales.fixtures import MISSING_ALLERGY_MESSAGE, UNSUPPORTED_MESSAGE
    with TestClient(app) as client:
        headers = login(client)
        def preview(message, answers):
            return client.post("/api/order-requests", json={"message": message, "clarifications": answers}, headers=headers)
        first = preview(MISSING_ALLERGY_MESSAGE, []).json()
        assert first["summary"]["allergen_concerns"] is None
        assert first["confirmationToken"] is None
        assert first["questions"]
        assert preview(MISSING_ALLERGY_MESSAGE, ["not sure"]).json()["confirmationToken"] is None
        complete = preview(MISSING_ALLERGY_MESSAGE, ["not sure", "no allergy concerns"]).json()
        assert complete["confirmationToken"]
        assert client.get("/api/orders/mine").json()["orders"] == []
        unsupported = preview(UNSUPPORTED_MESSAGE, []).json()
        assert "Unsupported product" in " ".join(unsupported["issues"])
        revised = preview(UNSUPPORTED_MESSAGE, ["24 vanilla birthday cupcakes instead"]).json()
        assert revised["confirmationToken"]
        saved = client.post("/api/order-requests/confirm", json={"confirmationToken": complete["confirmationToken"]}, headers=headers)
        assert saved.status_code == 200 and saved.json()["status"] == "Draft"
        assert preview(MISSING_ALLERGY_MESSAGE, ["no allergy concerns"] * 9).status_code == 422
