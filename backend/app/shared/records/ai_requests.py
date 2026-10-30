"""Saved evidence of every AI operation (architecture §5.3 [R5]; BAKE-37).

One row per business AI operation, which may take up to three attempts
(BAKE-8's budget). ``app.workflow.ai_requests`` writes it; it is operational
evidence, not a business record, so it is not audited and never takes part in
a business transaction.

It holds no prompt, reply or customer text and no provider key: only the task,
its subject, who asked, the provider and model, the prompt version, and each
attempt's time, result and error.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.records._common import Json, Timestamp, new_id, one_of, utcnow


class AIRequestStatus(StrEnum):
    # Saved before the first model call; a row left Running was cut off.
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    # No usable result within the budget.
    FAILED = "Failed"
    # Stopped by an error that is not the AI's, such as a bug in our code (BAKE-41).
    INTERRUPTED = "Interrupted"


class AIRequest(Base):
    __tablename__ = "ai_requests"
    __table_args__ = (
        one_of("status", AIRequestStatus, "status"),
        CheckConstraint("attempt_count BETWEEN 0 AND 3", name="attempt_count_in_budget"),
        CheckConstraint("latency_ms >= 0", name="latency_nonnegative"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    # BAKE-8's event type, such as "explainShortages".
    task_kind: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    subject_version: Mapped[int] = mapped_column(Integer)
    # A user ID, or a service name for work nobody asked for directly.
    actor: Mapped[str] = mapped_column(String(64))
    # "mock" in Mock mode, otherwise the provider's host name.
    provider: Mapped[str] = mapped_column(String(200))
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=AIRequestStatus.RUNNING)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    # The whole operation, from the row being saved until it closed.
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    # One entry per attempt: {"attempt", "latencyMs", "result", "error"?}.
    outcome: Mapped[list] = mapped_column(Json, default=list)
    started_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(Timestamp)
