"""UC-06 ledger HTTP boundary (BAKE-24). Owner only.

``/api/finance`` is an owner-only route family: BAKE-14's middleware checks the
session, the owner role and the CSRF token for every request under it. Each
handler also declares the owner dependency, as BAKE-14 asks of owner handlers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.session import get_sessionmaker
from app.pricing_bookkeeping.ledger import (
    DuplicateTransaction,
    LedgerEntry,
    LedgerService,
    RequestKeyConflict,
    TransactionInput,
    TransactionRejected,
)
from app.pricing_bookkeeping.quotation import ValidationIssue
from app.shared.store import StoreError, ValidatedRecordStore


@dataclass(frozen=True)
class OwnerIdentity:
    user_id: uuid.UUID


def current_owner(request: Request) -> OwnerIdentity:
    # Fails closed until BAKE-14 (#14) is merged; then this becomes
    # app.auth.access.require_owner. No identity is accepted from the request.
    raise HTTPException(401, "Please sign in.")


class TransactionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: str = Field(max_length=16)
    # A decimal string such as "12.50", never a JSON number, so no float is involved.
    amount: str = Field(max_length=32)
    sourceType: str = Field(max_length=32)
    sourceReference: str = Field(max_length=400)
    occurredAt: datetime
    orderId: uuid.UUID | None = None
    productId: uuid.UUID | None = None
    requestKey: uuid.UUID | None = None


def entry_json(entry: LedgerEntry) -> dict:
    return {
        "id": str(entry.transaction_id),
        "kind": entry.kind,
        "amount": str(entry.amount),
        "sourceType": entry.source_type,
        "sourceReference": entry.source_reference,
        "occurredAt": entry.occurred_at.isoformat(),
        "orderId": str(entry.order_id) if entry.order_id else None,
        "productId": str(entry.product_id) if entry.product_id else None,
    }


def issues_json(issues: tuple[ValidationIssue, ...]) -> list[dict]:
    return [{"field": issue.field, "message": issue.message} for issue in issues]


def make_router(settings: Settings, *, store: ValidatedRecordStore | None = None) -> APIRouter:
    router = APIRouter()

    def ledger() -> LedgerService:
        actual_store = store
        if actual_store is None:
            if not settings.database_url:
                raise HTTPException(503, "Database not configured.")
            actual_store = ValidatedRecordStore(get_sessionmaker(settings.database_url))
        return LedgerService(actual_store)

    @router.post("/api/finance/transactions")
    def record(body: TransactionBody, owner: OwnerIdentity = Depends(current_owner)):
        try:
            amount = Decimal(body.amount)
        except InvalidOperation:
            amount = body.amount  # reported by validation as "must be a Decimal"
        entry = TransactionInput(
            kind=body.kind,
            amount=amount,
            source_type=body.sourceType,
            source_reference=body.sourceReference,
            occurred_at=body.occurredAt,
            order_id=body.orderId,
            product_id=body.productId,
            request_key=body.requestKey,
        )
        try:
            saved = ledger().record(owner.user_id, entry)
        except TransactionRejected as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": "Some details need fixing. Nothing was recorded.",
                         "issues": issues_json(exc.issues)},
            )
        except DuplicateTransaction as exc:
            return JSONResponse(
                status_code=409,
                content={"detail": f"{exc.existing.source_type} {exc.existing.source_reference}"
                                   " is already recorded. Nothing was changed.",
                         "existing": entry_json(exc.existing)},
            )
        except RequestKeyConflict:
            raise HTTPException(
                409, "This request was already submitted with different details. Nothing was recorded."
            ) from None
        except (StoreError, SQLAlchemyError):
            raise HTTPException(
                503, "The transaction could not be saved. It was not recorded; please retry."
            ) from None
        return JSONResponse(status_code=200 if saved.replayed else 201, content=entry_json(saved))

    @router.get("/api/finance/transactions")
    def transactions(owner: OwnerIdentity = Depends(current_owner)):
        try:
            return {"transactions": [entry_json(entry) for entry in ledger().transactions()]}
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "The ledger could not be loaded; please retry.") from None

    return router
