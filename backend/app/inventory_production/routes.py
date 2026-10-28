"""Inventory HTTP boundary (BAKE-31, BAKE-32). Owner only.

``/api/inventory`` is an owner-only route family in the auth middleware, which
also checks the CSRF token on every POST; each handler also declares
``require_owner``.

- A shopping-list explanation that fails is still a 200 response with the
  saved list and the unavailable notice (UC-03 6a).
- UC-03 7a: the owner records a delivered ingredient (201), and a repeat of the
  same receipt returns it again (200) without raising stock twice. Stock
  levels list what is on hand, committed to released plans and available.
  Quantities are decimal strings both ways, never JSON numbers.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access import Identity, require_owner
from app.config import Settings
from app.db.session import get_sessionmaker
from app.inventory_production.fixtures import MockShortageExplainer
from app.inventory_production.inventory import (
    InventoryService,
    ReceiptConflict,
    ReceiptInput,
    ReceiptRejected,
    StockReceipt,
)
from app.inventory_production.shortages import ShortageExplainer
from app.llm.gateway import LLMCallFailed, LLMGateway
from app.shared.store import RecordNotFound, StaleVersionError, StoreError, ValidatedRecordStore

SOURCE_LABELS = {"Invoice": "Invoice", "DeliveryNote": "Delivery note"}


@dataclass(frozen=True)
class _NoProvider:
    """Live mode without usable provider settings: every attempt fails, so 6a applies."""

    reason: str

    def complete(self, prompt: str) -> str:
        raise LLMCallFailed(self.reason)


class ReceiptBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    itemId: uuid.UUID
    # A decimal string in the item's stock unit, such as "2500.000"; never a
    # JSON number, so no float is involved.
    quantity: str = Field(max_length=32)
    sourceType: str = Field(max_length=32)
    sourceReference: str = Field(max_length=400)


def receipt_json(receipt: StockReceipt) -> dict:
    return {
        "id": str(receipt.receipt_id),
        "itemId": str(receipt.item_ref.record_id),
        "ingredient": receipt.ingredient,
        "unit": receipt.unit,
        "quantity": str(receipt.quantity),
        "sourceType": receipt.source_type,
        "sourceReference": receipt.source_reference,
        "receivedAt": receipt.received_at.isoformat(),
        # Stock on hand now, after this receipt.
        "onHand": str(receipt.on_hand),
    }


def make_router(settings: Settings, *, store: ValidatedRecordStore | None = None,
                client_factory: Callable | None = None) -> APIRouter:
    router = APIRouter()

    def actual_store() -> ValidatedRecordStore:
        if store is not None:
            return store
        if not settings.database_url:
            raise HTTPException(503, "Database not configured.")
        return ValidatedRecordStore(get_sessionmaker(settings.database_url))

    def explainer() -> ShortageExplainer:
        records = actual_store()
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
        return ShortageExplainer(records, client)

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

    @router.get("/api/inventory/items")
    def items(owner: Identity = Depends(require_owner)):
        try:
            levels = InventoryService(actual_store()).stock_levels()
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "Stock levels could not be loaded. Please try again.") from None
        return {
            "items": [
                {
                    "id": str(level.item_id),
                    "ingredient": level.ingredient,
                    "unit": level.unit,
                    "onHand": str(level.on_hand),
                    "committed": str(level.committed),
                    "available": str(level.available),
                }
                for level in levels
            ]
        }

    @router.post("/api/inventory/receipts")
    def receive(body: ReceiptBody, owner: Identity = Depends(require_owner)):
        try:
            quantity = Decimal(body.quantity)
        except InvalidOperation:
            quantity = body.quantity  # reported by validation as "must be a Decimal"
        receipt = ReceiptInput(
            item_id=body.itemId,
            quantity=quantity,
            source_type=body.sourceType,
            source_reference=body.sourceReference,
        )
        try:
            saved = InventoryService(actual_store()).receive_stock(receipt, actor_id=owner.user_id)
        except ReceiptRejected as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": "Some details need fixing. Stock was not changed.",
                         "issues": [{"field": i.field, "message": i.message} for i in exc.issues]},
            )
        except ReceiptConflict as exc:
            existing = exc.existing
            return JSONResponse(
                status_code=409,
                content={"detail": f"{SOURCE_LABELS.get(existing.source_type, existing.source_type)}"
                                   f" {existing.source_reference} already recorded {existing.quantity}"
                                   f" {existing.unit} of {existing.ingredient}. Stock was not changed.",
                         "existing": receipt_json(existing)},
            )
        except RecordNotFound:
            raise HTTPException(404, "Ingredient not found.") from None
        except (StoreError, SQLAlchemyError):
            raise HTTPException(
                503, "The receipt could not be saved. Stock was not changed; please retry."
            ) from None
        return JSONResponse(
            status_code=200 if saved.replayed else 201,
            content=receipt_json(saved) | {"replayed": saved.replayed},
        )

    return router
