"""UC-03 step 6 HTTP boundary: the owner asks for a shopping-list explanation (BAKE-31).

``/api/inventory`` is owner-only in the auth middleware; the handler also
declares ``require_owner``. A failed explanation is still a 200 response with
the saved list and the unavailable notice (UC-03 6a).
"""

import uuid
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access import Identity, require_owner
from app.config import Settings
from app.db.session import get_sessionmaker
from app.inventory_production.fixtures import MockShortageExplainer
from app.inventory_production.shortages import ShortageExplainer
from app.llm.gateway import LLMCallFailed, LLMGateway
from app.shared.store import RecordNotFound, StaleVersionError, StoreError, ValidatedRecordStore


@dataclass(frozen=True)
class _NoProvider:
    """Live mode without usable provider settings: every attempt fails, so 6a applies."""

    reason: str

    def complete(self, prompt: str) -> str:
        raise LLMCallFailed(self.reason)


def make_router(settings: Settings, *, store: ValidatedRecordStore | None = None,
                client_factory: Callable | None = None) -> APIRouter:
    router = APIRouter()

    def explainer() -> ShortageExplainer:
        actual_store = store
        if actual_store is None:
            if not settings.database_url:
                raise HTTPException(503, "Database not configured.")
            actual_store = ValidatedRecordStore(get_sessionmaker(settings.database_url))
        if client_factory is not None:
            client = client_factory()
        elif settings.llm_mode == "mock":
            client = MockShortageExplainer()
        else:
            try:
                client = LLMGateway.from_environment()
            except (LLMCallFailed, ValueError) as exc:
                # Missing or unusable settings, such as a URL urlsplit cannot read.
                client = _NoProvider(str(exc))
        return ShortageExplainer(actual_store, client)

    @router.post("/api/inventory/shopping-lists/{list_id}/explanation")
    def explain(list_id: uuid.UUID, owner: Identity = Depends(require_owner)):
        try:
            outcome = explainer().explain(list_id, actor_id=owner.user_id)
        except RecordNotFound:
            raise HTTPException(404, "Shopping list not found.") from None
        except StaleVersionError:
            raise HTTPException(
                409, "The order changed after this list was saved. Run the stock check again."
            ) from None
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "The shopping list could not be read. Please try again.") from None
        return {
            "shoppingListId": str(outcome.shopping_list_ref.record_id),
            "order": outcome.order_ref.to_json(),
            "status": outcome.status,
            "explanation": outcome.text or None,
            "notice": outcome.notice or None,
            "lines": [
                {
                    "itemId": str(line.item_id),
                    "ingredient": line.ingredient,
                    "unit": line.unit,
                    "required": str(line.required),
                    "shortBy": None if line.short_by is None else str(line.short_by),
                }
                for line in outcome.lines
            ],
        }

    return router
