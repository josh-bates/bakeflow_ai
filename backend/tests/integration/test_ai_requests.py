"""BAKE-37: every AI operation is saved in ai_requests [R5].

``run_ai_task`` saves the row before the first model call, updates it after
each attempt and closes it with the outcome, in transactions of its own, so a
business rollback keeps it. Inside the app every request has the log bound,
so existing AI features are recorded without any change.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.db.session import get_sessionmaker
from app.llm.gateway import LLMCallFailed, LLMGateway
from app.main import create_app
from app.shared.records import AIRequest, AIRequestStatus
from app.shared.refs import RecordRef
from app.workflow.ai_requests import REJECTED, AIRequestLog, bound
from app.workflow.order_flow import AIOperationFailed, OrderStage, WorkflowService

SUBJECT = RecordRef("ShoppingList", uuid.UUID("5c1d2e3f-0000-0000-0000-000000000001"), 1)
REPLY = "Flour is short for the order of Alex Example on 02 Oct."


@pytest.fixture
def log(database_url, store) -> AIRequestLog:
    return AIRequestLog(get_sessionmaker(database_url), provider="api.example.test", model="example-model")


def rows(store) -> list[AIRequest]:
    with store.unit_of_work() as session:
        return list(session.scalars(select(AIRequest).order_by(AIRequest.started_at)))


def run(log, attempt, usable=lambda reply: True, **kwargs):
    with bound(log):
        return WorkflowService().run_ai_task(
            SUBJECT, OrderStage.DRAFT, attempt, usable,
            actor="owner-1", event_type="explainShortages", **kwargs,
        )


def test_a_retried_success_is_saved_with_each_attempt(log, store):
    replies = iter(["bad", "good"])

    result = run(log, lambda: next(replies), lambda reply: reply == "good",
                 prompt_version="explain_shortages/1")

    assert result == "good"
    [row] = rows(store)
    assert (row.status, row.attempt_count) == (AIRequestStatus.SUCCEEDED, 2)
    assert [entry["result"] for entry in row.outcome] == ["rejected", "usable"]
    assert [entry["attempt"] for entry in row.outcome] == [1, 2]
    assert row.last_error == REJECTED
    assert (row.task_kind, row.actor, row.prompt_version) == ("explainShortages", "owner-1", "explain_shortages/1")
    assert (row.subject_type, row.subject_id, row.subject_version) == ("ShoppingList", SUBJECT.record_id, 1)
    assert (row.provider, row.model) == ("api.example.test", "example-model")
    assert row.latency_ms >= 0 and row.finished_at >= row.started_at


def test_an_exhausted_budget_is_saved_as_failed_with_the_last_error(log, store):
    def attempt():
        raise LLMCallFailed("provider call failed: TimeoutError: timed out")

    with pytest.raises(AIOperationFailed):
        run(log, attempt)

    [row] = rows(store)
    assert (row.status, row.attempt_count) == (AIRequestStatus.FAILED, 3)
    assert [entry["result"] for entry in row.outcome] == ["error"] * 3
    assert row.last_error == "LLMCallFailed"
    assert row.prompt_version is None


def test_a_bug_in_our_code_is_saved_as_interrupted_not_as_an_ai_failure(log, store):
    def attempt():
        raise TypeError("unsupported operand type(s)")

    with pytest.raises(TypeError):
        run(log, attempt)

    [row] = rows(store)
    assert (row.status, row.attempt_count) == (AIRequestStatus.INTERRUPTED, 0)
    assert row.last_error == "TypeError"


def test_the_row_is_saved_before_the_first_model_call(log, store):
    seen = []

    def attempt():
        seen.extend((row.status, row.attempt_count) for row in rows(store))
        return "good"

    run(log, attempt)

    assert seen == [(AIRequestStatus.RUNNING, 0)]


def test_the_record_survives_a_business_rollback(log, store):
    with pytest.raises(RuntimeError):
        with store.unit_of_work():
            run(log, lambda: "good")
            raise RuntimeError("the business save failed")

    assert [row.status for row in rows(store)] == [AIRequestStatus.SUCCEEDED]


def test_no_reply_or_customer_text_is_saved(log, store):
    with pytest.raises(AIOperationFailed):
        run(log, lambda: REPLY, lambda reply: False)

    [row] = rows(store)
    saved = json.dumps({column.name: str(getattr(row, column.name)) for column in AIRequest.__table__.columns})
    assert "Alex Example" not in saved and "Flour" not in saved


def test_no_error_text_is_saved_as_it_may_quote_the_key_or_the_reply(log, store, monkeypatch):
    key = "sk-synthetic-never-saved"
    gateway = LLMGateway("https://api.example.test/v1", key, "example-model")

    def refused(*args, **kwargs):
        raise urllib.error.HTTPError(gateway.base_url, 401, f"Invalid API key {key}", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", refused)
    with pytest.raises(AIOperationFailed):
        run(log, lambda: gateway.complete("prompt"))
    # Not retried, so saved as Interrupted; its first line quotes the reply.
    with pytest.raises(ValueError):
        run(log, lambda: REPLY, lambda reply: int(reply) > 0)

    failed, interrupted = rows(store)
    assert failed.last_error == "LLMCallFailed (HTTPError)"
    assert {entry["error"] for entry in failed.outcome} == {"LLMCallFailed (HTTPError)"}
    assert interrupted.last_error == "ValueError"
    saved = json.dumps(
        [{column.name: str(getattr(row, column.name)) for column in AIRequest.__table__.columns}
         for row in (failed, interrupted)]
    )
    assert key not in saved and "Alex Example" not in saved


def test_concurrent_operations_get_separate_rows(log, store):
    threads = [threading.Thread(target=run, args=(log, lambda: "good")) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    saved = rows(store)
    assert len({row.request_id for row in saved}) == 4
    assert {row.status for row in saved} == {AIRequestStatus.SUCCEEDED}


def test_outside_a_request_nothing_is_saved(store):
    WorkflowService().run_ai_task(
        SUBJECT, OrderStage.DRAFT, lambda: "good", lambda reply: True,
        actor="owner-1", event_type="explainShortages",
    )

    assert rows(store) == []


# --- through the app: existing features are recorded unchanged -------------


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


def test_an_existing_ai_feature_is_recorded_and_the_owner_can_list_it(app, store):
    from app.sales.fixtures import DEMO_MESSAGE

    with TestClient(app) as client:
        preview = client.post("/api/order-requests", json={"message": DEMO_MESSAGE}, headers=login(client))
        assert preview.status_code == 200, preview.text
        customer_view = client.get("/api/owner/ai-requests")
        login(client, "owner")
        listed = client.get("/api/owner/ai-requests")

    [row] = rows(store)
    assert (row.task_kind, row.status, row.provider, row.model) == (
        "extractOrder", AIRequestStatus.SUCCEEDED, "mock", "mock"
    )
    assert DEMO_MESSAGE not in json.dumps(row.outcome) + (row.last_error or "")
    assert customer_view.status_code == 403
    assert listed.status_code == 200
    [item] = listed.json()["requests"]
    assert item["id"] == str(row.request_id)
    assert (item["task"], item["status"], item["attempts"]) == ("extractOrder", "Succeeded", 1)
    assert item["subject"]["recordType"] == "OrderIntake"
    assert item["outcome"][0]["result"] == "usable"


def test_the_owner_list_is_newest_first_and_limited(app, log, store):
    for _ in range(3):
        run(log, lambda: "good")
    with store.unit_of_work() as session:
        assert session.scalar(select(func.count()).select_from(AIRequest)) == 3

    with TestClient(app) as client:
        login(client, "owner")
        listed = client.get("/api/owner/ai-requests", params={"limit": 2}).json()["requests"]
        too_many = client.get("/api/owner/ai-requests", params={"limit": 201})

    newest = sorted(rows(store), key=lambda row: row.started_at, reverse=True)
    assert [item["id"] for item in listed] == [str(row.request_id) for row in newest[:2]]
    assert too_many.status_code == 422
