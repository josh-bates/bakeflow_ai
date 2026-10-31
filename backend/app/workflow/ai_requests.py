"""Saving every AI operation as evidence (architecture §5.3 [R5]; BAKE-37).

``WorkflowService.run_ai_task`` reports each operation here: its
``ai_requests`` row is saved before the first model call, updated after every
attempt and closed with the outcome. Each write is its own short transaction,
so the evidence stays when the business transaction around the AI call rolls
back. Writing is best effort: if the database refuses a write, the failure is
logged and the AI operation carries on with the same result. An error is saved
by its type and the type of its cause only: its text may quote the reply, the
customer's words or the provider's key.

The app binds one ``AIRequestLog`` to every HTTP request (``AIRequestMiddleware``),
so no caller has to pass it along. Code running outside a request, such as a
unit test, has none and keeps only BAKE-8's in-memory audit log.

Not built from the architecture: the regeneration token
(``generation_index``/``active_request_id``, [R10]) and ``input_refs``. Each
module already refuses a stale result with its own version checks.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.session import get_sessionmaker
from app.shared.records import AIRequest, AIRequestStatus
from app.shared.records._common import utcnow
from app.shared.refs import RecordRef

REJECTED = "the reply did not pass the checks"

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIRequestLog:
    """Where this app saves its AI operations, and which model it calls."""

    sessions: sessionmaker[Session]
    provider: str
    model: str

    @classmethod
    def for_settings(cls, settings: Settings) -> AIRequestLog | None:
        """The log for the app's database and model; None without a database."""
        if not settings.database_url:
            return None
        if settings.llm_mode == "mock":
            provider = model = "mock"
        else:
            # The same variables LLMGateway reads (BAKE-9); the key is never read here.
            try:
                provider = urlsplit(os.environ.get("BAKEFLOW_LLM_BASE_URL", "")).hostname or "unset"
            except ValueError:
                # The gateway refuses this URL too, so every attempt fails (UC 6a paths).
                provider = "unreadable"
            model = os.environ.get("BAKEFLOW_LLM_MODEL") or "unset"
        return cls(get_sessionmaker(settings.database_url), provider, model)


_current: ContextVar[AIRequestLog | None] = ContextVar("ai_request_log", default=None)


@contextmanager
def bound(log: AIRequestLog | None) -> Iterator[None]:
    """Save the AI operations started inside this block to ``log``."""
    token = _current.set(log)
    try:
        yield
    finally:
        _current.reset(token)


class AIRequestMiddleware:
    """Binds the app's log for each request, before any route runs."""

    def __init__(self, app, log: AIRequestLog | None) -> None:
        self.app = app
        self.log = log

    async def __call__(self, scope, receive, send) -> None:
        with bound(self.log):
            await self.app(scope, receive, send)


class Recording:
    """One AI operation as it runs; without a bound log it saves nothing."""

    def __init__(self, subject: RecordRef, task_kind: str, *, actor: str, prompt_version: str | None):
        self._log = _current.get()
        self._began = time.perf_counter()
        self._values = {
            "request_id": uuid.uuid4(),
            "task_kind": task_kind[:64],
            "subject_type": subject.record_type[:64],
            "subject_id": subject.record_id,
            "subject_version": subject.version,
            "actor": actor[:64],
            "provider": self._log.provider if self._log else "",
            "model": self._log.model if self._log else "",
            "prompt_version": prompt_version[:64] if prompt_version else None,
            "status": AIRequestStatus.RUNNING,
            "attempt_count": 0,
            "outcome": [],
            "started_at": utcnow(),
        }
        self._save()

    @property
    def request_id(self) -> uuid.UUID:
        return self._values["request_id"]

    def attempt(self, began: float, *, usable: bool = False, error: BaseException | None = None) -> None:
        """One attempt that started at ``began`` (``time.perf_counter()``) has ended."""
        entry = {"attempt": self._values["attempt_count"] + 1, "latencyMs": _since(began)}
        if error is not None:
            entry |= {"result": "error", "error": describe(error)}
            self._values["last_error"] = entry["error"]
        elif usable:
            entry["result"] = "usable"
        else:
            entry["result"] = "rejected"
            self._values["last_error"] = REJECTED
        self._values["outcome"] = [*self._values["outcome"], entry]
        self._values["attempt_count"] = entry["attempt"]
        self._save()

    def finish(self, status: AIRequestStatus, *, error: BaseException | None = None) -> None:
        if error is not None:
            self._values["last_error"] = describe(error)
        self._values |= {"status": status, "latency_ms": _since(self._began), "finished_at": utcnow()}
        self._save()

    def _save(self) -> None:
        if self._log is None:
            return
        try:
            with self._log.sessions.begin() as session:
                session.merge(AIRequest(**self._values))
        except SQLAlchemyError:
            _log.warning("AI request %s could not be saved", self.request_id, exc_info=True)


def describe(error: BaseException) -> str:
    """The error's type, and its cause's ("LLMCallFailed (HTTPError)"); never its text."""
    cause = error.__cause__
    return type(error).__name__ if cause is None else f"{type(error).__name__} ({type(cause).__name__})"


def _since(began: float) -> int:
    return max(0, round((time.perf_counter() - began) * 1000))
