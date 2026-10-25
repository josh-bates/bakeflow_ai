"""BAKE-34: the profitability route is owner-only and checks its period."""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.access import Identity
from app.config import Settings
from app.pricing_bookkeeping.routes import make_router

URL = "/api/finance/profitability"


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


def test_no_session_is_refused():
    assert client_as(None).get(URL).status_code == 401


def test_a_customer_is_refused():
    assert client_as("Customer").get(URL).status_code == 403


def test_period_bounds_need_a_time_zone():
    response = client_as("Owner").get(URL, params={"start": "2026-10-01T00:00:00"})
    assert response.status_code == 422
    assert "time zone" in response.json()["detail"]


def test_period_end_must_follow_its_start():
    response = client_as("Owner").get(URL, params={
        "start": "2026-10-08T00:00:00+11:00", "end": "2026-10-01T00:00:00+10:00"})
    assert response.status_code == 422
