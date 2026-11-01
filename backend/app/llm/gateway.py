


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

import http.client
import json
import re
import os
import urllib.parse
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
    # Some models wrap the JSON in a Markdown code fence; the object inside is what was asked for.
    fenced = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n?\s*```\s*", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
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


DEFAULT_TIMEOUT_SECONDS = 90
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
    # Extra request fields a provider understands, such as DeepSeek's switch for its reasoning step.
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a provider URL that would send the API key unencrypted.

        The key travels in an Authorization header, so plain http would put it
        on the wire in the clear. Checked at construction, before any call.
        """
        scheme = urllib.parse.urlsplit(self.base_url).scheme
        if scheme != "https":
            raise LLMCallFailed(
                "provider base_url must use https, not"
                f" {scheme or 'a missing scheme'}: {self.base_url!r}"
            )

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
            # Opt-in, because other providers reject a field they don't know.
            extra={"thinking": {"type": "disabled"}}
            if os.environ.get("BAKEFLOW_LLM_THINKING") == "off"
            else {},
        )

    def complete(self, prompt: str) -> str:
        """Make exactly one provider call and return the reply text."""
        payload = json.dumps(
            {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                **self.extra,
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
        except (OSError, http.client.HTTPException) as exc:
            # OSError covers urllib's URLError and HTTPError, socket timeouts
            # and connection resets; HTTPException covers a reply that stops
            # early (IncompleteRead). Callers handle one exception type.
            raise LLMCallFailed(
                f"provider call failed: {type(exc).__name__}: {exc}"
            ) from exc
        except UnicodeDecodeError as exc:
            raise LLMCallFailed("provider returned bytes that are not UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise LLMCallFailed(f"provider returned non-JSON: {exc}") from exc

        try:
            reply = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMCallFailed("provider response had an unexpected shape") from exc

        if not isinstance(reply, str) or not reply.strip():
            raise LLMCallFailed("provider returned empty output")
        return reply