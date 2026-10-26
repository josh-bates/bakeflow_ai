"""BAKE-21: the production plan routes are owner-only and check their input."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.access import Identity
from app.config import Settings
from app.inventory_production.routes import make_router

PLAN = {"startDate": "2026-11-05", "endDate": "2026-11-05"}
DECISION = {"version": 1, "decision": "Approved"}


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


ROUTES = [
    ("get", "/api/schedules", None),
    ("post", "/api/schedules", PLAN),
    ("post", f"/api/schedules/{uuid.uuid4()}/revisions", PLAN),
    ("post", f"/api/schedules/{uuid.uuid4()}/decision", DECISION),
]


@pytest.mark.parametrize(("method", "url", "body"), ROUTES)
@pytest.mark.parametrize(("role", "status"), [(None, 401), ("Customer", 403)])
def test_only_the_owner_may_use_plans(method, url, body, role, status):
    response = getattr(client_as(role), method)(url, **({"json": body} if body else {}))
    assert response.status_code == status


def test_last_day_must_not_be_before_the_first():
    response = client_as("Owner").post("/api/schedules", json={**PLAN, "startDate": "2026-11-06"})
    assert response.status_code == 422
    assert "last day is before its first day" in response.json()["detail"]


def test_a_plan_covers_at_most_two_weeks():
    response = client_as("Owner").post("/api/schedules", json={**PLAN, "endDate": "2026-11-19"})
    assert response.status_code == 422
    assert "at most 14 days" in response.json()["detail"]


@pytest.mark.parametrize("body", [{"version": 0, "decision": "Approved"}, {"version": 1, "decision": "Maybe"},
                                  {**DECISION, "releasedVersion": 1}])
def test_decision_must_name_one_version_and_a_known_decision(body):
    url = f"/api/schedules/{uuid.uuid4()}/decision"
    assert client_as("Owner").post(url, json=body).status_code == 422
