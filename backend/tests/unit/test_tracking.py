import json

import pytest

from app.llm.gateway import MockLLMClient
from app.sales import tracking
from app.shared.records import OrderStatus


@pytest.mark.parametrize("status", list(OrderStatus))
def test_each_recorded_stage_has_grounded_explanation(status, monkeypatch):
    client = MockLLMClient(default_reply=json.dumps({"status": status, "explanation": tracking.EXPLANATIONS[status][0]}))
    monkeypatch.setattr(tracking, "create_client", lambda *_: client)
    result = tracking.explain_record(status, 1, "order", "customer", "mock")
    assert result["explanationAvailable"] is True
    assert result["explanation"] in tracking.EXPLANATIONS[status]
    assert len(client.calls) == 1


@pytest.mark.parametrize("reply", ["not JSON", '{"status":"Completed","explanation":"Ready now"}',
    '{"status":"Draft","explanation":"Your order is completed and paid for."}'])
def test_unusable_explanation_has_bounded_retries_and_fallback(reply, monkeypatch):
    client = MockLLMClient(default_reply=reply)
    monkeypatch.setattr(tracking, "create_client", lambda *_: client)
    result = tracking.explain_record("Draft", 1, "order", "customer", "mock")
    assert result == {"explanation": tracking.UNAVAILABLE, "explanationAvailable": False}
    assert len(client.calls) == 3


def test_retry_can_recover_without_multiplying_budget(monkeypatch):
    client = MockLLMClient(replies=["bad", json.dumps({"status": "Draft", "explanation": tracking.EXPLANATIONS["Draft"][1]})])
    monkeypatch.setattr(tracking, "create_client", lambda *_: client)
    assert tracking.explain_record("Draft", 1, "order", "customer", "mock")["explanationAvailable"]
    assert len(client.calls) == 2


def test_provider_connection_failure_returns_fallback(monkeypatch):
    calls = []
    class UnavailableClient:
        def complete(self, prompt):
            calls.append(prompt)
            raise TimeoutError("private provider failure")
    monkeypatch.setattr(tracking, "create_client", lambda *_: UnavailableClient())
    result = tracking.explain_record("Draft", 1, "order", "customer", "live")
    assert not result["explanationAvailable"]
    assert "private" not in result["explanation"]
    assert len(calls) == 3
