"""BAKE-37: saving AI operations never changes an operation's result [R5].

No database is needed: the persistence itself is in
``tests/integration/test_ai_requests.py``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.shared.refs import RecordRef
from app.llm.gateway import LLMCallFailed
from app.workflow.ai_requests import AIRequestLog, bound, describe
from app.workflow.order_flow import AIOperationFailed, OrderStage, WorkflowService

SUBJECT = RecordRef("Order", uuid.uuid4(), 1)


class _BrokenSessions:
    """A database that refuses every write."""

    def begin(self):
        raise OperationalError("INSERT", {}, Exception("connection refused"))


def test_a_database_that_refuses_the_record_does_not_change_the_result(caplog):
    with bound(AIRequestLog(_BrokenSessions(), "mock", "mock")):
        service = WorkflowService()
        result = service.run_ai_task(
            SUBJECT, OrderStage.DRAFT, lambda: "good", lambda reply: True,
            actor="owner", event_type="explainShortages",
        )
        with pytest.raises(AIOperationFailed):
            service.run_ai_task(
                SUBJECT, OrderStage.DRAFT, lambda: "bad", lambda reply: False,
                actor="owner", event_type="explainShortages",
            )

    assert result == "good"
    assert "could not be saved" in caplog.text
    assert [entry.event_type for entry in service.audit_log] == ["explainShortagesFailed"]


def test_an_error_is_described_by_its_type_and_its_cause_only():
    error = ValueError("invalid literal for int() with base 10: 'Alex, 0400 000 000'")
    try:
        raise LLMCallFailed("provider call failed: HTTPError: Invalid API key sk-x") from TimeoutError("sk-x")
    except LLMCallFailed as exc:
        wrapped = exc

    assert describe(error) == "ValueError"
    assert describe(wrapped) == "LLMCallFailed (TimeoutError)"
    assert describe(RuntimeError()) == "RuntimeError"


def test_the_log_names_the_provider_host_and_model_but_never_reads_the_key(monkeypatch):
    monkeypatch.setenv("BAKEFLOW_LLM_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("BAKEFLOW_LLM_MODEL", "example-model")
    monkeypatch.setenv("BAKEFLOW_LLM_API_KEY", "sk-not-to-be-read")
    url = "postgresql+psycopg://user:pw@127.0.0.1:1/none"

    live = AIRequestLog.for_settings(Settings(_env_file=None, database_url=url, llm_mode="live"))
    mock = AIRequestLog.for_settings(Settings(_env_file=None, database_url=url, llm_mode="mock"))

    assert (live.provider, live.model) == ("api.example.test", "example-model")
    assert "sk-not-to-be-read" not in repr(live)
    assert (mock.provider, mock.model) == ("mock", "mock")
    assert AIRequestLog.for_settings(Settings(_env_file=None, database_url=None)) is None


@pytest.mark.parametrize(("base_url", "provider"), [(None, "unset"), ("https://[", "unreadable")])
def test_missing_or_unreadable_provider_settings_still_start_the_app(monkeypatch, base_url, provider):
    monkeypatch.delenv("BAKEFLOW_LLM_BASE_URL", raising=False)
    if base_url:
        monkeypatch.setenv("BAKEFLOW_LLM_BASE_URL", base_url)
    url = "postgresql+psycopg://user:pw@127.0.0.1:1/none"

    log = AIRequestLog.for_settings(Settings(_env_file=None, database_url=url, llm_mode="live"))

    assert log.provider == provider
