"""Tests for the LLM gateway, mock client and output validation (BAKE-9)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from app.llm import gateway
from app.llm.gateway import (
    InvalidLLMOutput,
    LLMCallFailed,
    LLMGateway,
    MockLLMClient,
    parse_structured_reply,
)

PROVIDER_SETTINGS = {
    "BAKEFLOW_LLM_BASE_URL": "https://example.test/v1",
    "BAKEFLOW_LLM_API_KEY": "test-key",
    "BAKEFLOW_LLM_MODEL": "test-model",
}


class _FakeResponse:
    """Stands in for the object urlopen returns."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _reply(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


# --- mock client -----------------------------------------------------------


def test_mock_client_returns_canned_replies_in_order():
    client = MockLLMClient(replies=["first", "second"])
    assert client.complete("a") == "first"
    assert client.complete("b") == "second"


def test_mock_client_records_every_prompt():
    client = MockLLMClient()
    client.complete("a")
    client.complete("b")
    assert client.calls == ["a", "b"]


# --- output validation -----------------------------------------------------


def test_valid_output_passes_through():
    parsed = parse_structured_reply('{"body": "hi", "platform": "ig"}', ("body", "platform"))
    assert parsed == {"body": "hi", "platform": "ig"}


@pytest.mark.parametrize(
    "bad",
    ["not json at all", "[1, 2, 3]", '{"body": "hi"}', '"just a string"'],
)
def test_malformed_output_is_reported_as_invalid(bad):
    with pytest.raises(InvalidLLMOutput):
        parse_structured_reply(bad, ("body", "platform"))


def test_invalid_output_is_a_kind_of_call_failure():
    with pytest.raises(LLMCallFailed):
        parse_structured_reply("nonsense", ("body",))


# --- the gateway -----------------------------------------------------------


def test_gateway_makes_exactly_one_provider_call(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        return _FakeResponse(_reply("hello"))

    monkeypatch.setattr(gateway.urllib.request, "urlopen", fake_urlopen)
    client = LLMGateway(base_url="https://example.test/v1", api_key="k", model_name="m")

    assert client.complete("prompt") == "hello"
    assert len(calls) == 1


def test_gateway_does_not_retry_on_failure(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        raise urllib.error.URLError("provider down")

    monkeypatch.setattr(gateway.urllib.request, "urlopen", fake_urlopen)
    client = LLMGateway(base_url="https://example.test/v1", api_key="k", model_name="m")

    with pytest.raises(LLMCallFailed):
        client.complete("prompt")
    assert len(calls) == 1


def test_gateway_rejects_empty_output(monkeypatch):
    monkeypatch.setattr(
        gateway.urllib.request, "urlopen",
        lambda request, timeout=None: _FakeResponse(_reply("   ")),
    )
    client = LLMGateway(base_url="https://example.test/v1", api_key="k", model_name="m")

    with pytest.raises(LLMCallFailed):
        client.complete("prompt")


def test_gateway_rejects_unexpected_response_shape(monkeypatch):
    monkeypatch.setattr(
        gateway.urllib.request, "urlopen",
        lambda request, timeout=None: _FakeResponse(b'{"unexpected": true}'),
    )
    client = LLMGateway(base_url="https://example.test/v1", api_key="k", model_name="m")

    with pytest.raises(LLMCallFailed):
        client.complete("prompt")


# --- configuration ---------------------------------------------------------


def test_from_environment_builds_a_gateway(monkeypatch):
    for name, value in PROVIDER_SETTINGS.items():
        monkeypatch.setenv(name, value)

    client = LLMGateway.from_environment()
    assert client.base_url == "https://example.test/v1"
    assert client.model_name == "test-model"


def test_from_environment_names_what_is_missing(monkeypatch):
    for name in PROVIDER_SETTINGS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(LLMCallFailed) as error:
        LLMGateway.from_environment()
    assert "BAKEFLOW_LLM_API_KEY" in str(error.value)


def test_api_key_is_not_printed(monkeypatch):
    client = LLMGateway(base_url="https://example.test/v1", api_key="secret", model_name="m")
    assert "secret" not in repr(client)