


"""Single server-side gateway to the external language model (BAKE-9).

Every agent reaches the LLM through ``ILLMClient``. ``LLMGateway`` calls a real
provider; ``MockLLMClient`` returns canned answers for tests and offline demos.

The gateway makes exactly one provider call per request and reports failure or
structurally invalid output. It never retries: the retry budget is owned by
WorkflowService (BAKE-8), so a loop here would multiply the attempt count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import json
import os
import urllib.error
import urllib.request


class LLMCallFailed(Exception):
    """Raised when the provider call did not produce usable text."""


class ILLMClient(Protocol):
    """The one shape every LLM client must have (class model: ILLMClient)."""

    def complete(self, prompt: str) -> str:
        """Send one prompt, return the model's text reply."""
        ...


@dataclass
class MockLLMClient:
    """Canned replies for tests and offline demos. Implements ILLMClient."""

    replies: list[str] = field(default_factory=list)
    default_reply: str = '{"ok": true}'
    calls: list[str] = field(default_factory=list)

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.replies:
            return self.replies.pop(0)
        return self.default_reply


class InvalidLLMOutput(LLMCallFailed):
    """Raised when the reply is not the structure the caller asked for."""


def parse_structured_reply(text: str, required_fields: tuple[str, ...]) -> dict:
    """Parse a JSON object reply and check the required fields are present."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidLLMOutput(f"reply was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise InvalidLLMOutput("reply was not a JSON object")

    missing = [name for name in required_fields if name not in parsed]
    if missing:
        raise InvalidLLMOutput(
            f"reply is missing required fields: {', '.join(missing)}"
        )

    return parsed


DEFAULT_TIMEOUT_SECONDS = 30
_PROVIDER_SETTINGS = (
    "BAKEFLOW_LLM_BASE_URL",
    "BAKEFLOW_LLM_API_KEY",
    "BAKEFLOW_LLM_MODEL",
)


@dataclass
class LLMGateway:
    """Calls the configured provider exactly once per request. Implements ILLMClient.

    Settings come from the environment, never from a committed file.

    This class does not retry. The retry budget belongs to WorkflowService
    (BAKE-8); a loop here would multiply the attempt count for one operation.
    """

    base_url: str
    api_key: str = field(repr=False)
    model_name: str = "unset"
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "LLMGateway":
        """Build a gateway from environment variables, or say what is missing."""
        missing = [name for name in _PROVIDER_SETTINGS if not os.environ.get(name)]
        if missing:
            raise LLMCallFailed(
                f"missing provider configuration: {', '.join(missing)}"
            )
        return cls(
            base_url=os.environ["BAKEFLOW_LLM_BASE_URL"],
            api_key=os.environ["BAKEFLOW_LLM_API_KEY"],
            model_name=os.environ["BAKEFLOW_LLM_MODEL"],
        )

    def complete(self, prompt: str) -> str:
        """Make exactly one provider call and return the reply text."""
        payload = json.dumps(
            {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise LLMCallFailed(f"provider call failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMCallFailed(f"provider returned non-JSON: {exc}") from exc

        try:
            reply = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMCallFailed("provider response had an unexpected shape") from exc

        if not isinstance(reply, str) or not reply.strip():
            raise LLMCallFailed("provider returned empty output")
        return reply