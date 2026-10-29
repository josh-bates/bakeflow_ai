"""The owner's view of saved AI operations (architecture [R5]; BAKE-37).

``GET /api/owner/ai-requests`` lists the latest operations, newest first:
which task ran on what, the provider and model, the prompt version, how many
attempts it took and how long, why attempts failed, and the outcome. Read
only; ``/api/owner`` is an owner-only route family and the handler also
declares ``require_owner``.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access import Identity, require_owner
from app.config import Settings
from app.db.session import get_sessionmaker
from app.shared.records import AIRequest
from app.shared.store import StoreError, ValidatedRecordStore


def request_json(row: AIRequest) -> dict:
    return {
        "id": str(row.request_id),
        "task": row.task_kind,
        "subject": {"recordType": row.subject_type, "recordId": str(row.subject_id),
                    "version": row.subject_version},
        "actor": row.actor,
        "provider": row.provider,
        "model": row.model,
        "promptVersion": row.prompt_version,
        "status": row.status,
        "attempts": row.attempt_count,
        "latencyMs": row.latency_ms,
        "lastError": row.last_error,
        "outcome": row.outcome,
        "startedAt": row.started_at.isoformat(),
        "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
    }


def make_router(settings: Settings, *, store: ValidatedRecordStore | None = None) -> APIRouter:
    router = APIRouter()

    def actual_store() -> ValidatedRecordStore:
        if store is not None:
            return store
        if not settings.database_url:
            raise HTTPException(503, "Database not configured.")
        return ValidatedRecordStore(get_sessionmaker(settings.database_url))

    @router.get("/api/owner/ai-requests")
    def ai_requests(limit: int = Query(50, ge=1, le=200), owner: Identity = Depends(require_owner)):
        try:
            with actual_store().snapshot() as session:
                rows = session.scalars(
                    select(AIRequest)
                    .order_by(AIRequest.started_at.desc(), AIRequest.request_id)
                    .limit(limit)
                ).all()
                return {"requests": [request_json(row) for row in rows]}
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "AI activity could not be loaded. Please try again.") from None

    return router
