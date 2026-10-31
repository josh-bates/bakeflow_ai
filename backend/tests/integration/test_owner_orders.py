"""The owner's list of every order, over HTTP (Orders page).

An accepted quote takes its order out of the quote queue; this list is where
the owner still sees it, with the accepted total and the payment state.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.integration.test_quote_routes import login, priced_order


@pytest.fixture
def app(database_url, seeded):
    return create_app(Settings(_env_file=None, database_url=database_url,
                               environment="test", app_secret="test-secret-" * 4))


def listed(client, order_id) -> dict:
    return next(o for o in client.get("/api/owner/orders").json()["orders"] if o["id"] == order_id)


def test_owner_sees_an_accepted_order_after_it_leaves_the_quote_queue(app, store, seeded):
    order_id = priced_order(store, seeded)
    with TestClient(app) as client:
        owner = login(client, "owner")
        before = listed(client, order_id)
        assert (before["status"], before["quote"], before["accepted"], before["payment"]) == ("Draft", None, False, None)
        assert before["summary"] == "24 Vanilla cupcake" and before["customer"]

        quote = client.post(f"/api/finance/orders/{order_id}/quotes", json={}, headers=owner).json()
        base = f"/api/finance/quotes/{quote['id']}/versions/1"
        assert client.post(f"{base}/approve", headers=owner).status_code == 200

        customer = login(client)
        accepted = client.post(f"/api/quotes/{quote['id']}/versions/1/accept", headers=customer)
        assert accepted.status_code == 200, accepted.text

        login(client, "owner")
        assert all(o["orderId"] != order_id for o in client.get("/api/finance/quotes").json()["orders"])
        after = listed(client, order_id)
        assert after["status"] == "Confirmed" and after["accepted"] is True
        assert after["quote"] == {"version": 1, "total": "94.23", "customerDecision": "Accepted"}
        assert after["payment"] is None


def test_only_the_owner_sees_every_order(app, store, seeded):
    priced_order(store, seeded)
    with TestClient(app) as client:
        assert client.get("/api/owner/orders").status_code == 401
        login(client)
        assert client.get("/api/owner/orders").status_code == 403
