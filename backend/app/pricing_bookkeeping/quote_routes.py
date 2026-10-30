"""UC-05 HTTP boundary: owner quote approval and customer acceptance (BAKE-23).

Owner routes sit in the owner-only ``/api/finance`` family (BAKE-14's
middleware checks the session, the owner role and the CSRF token) and also
declare ``Depends(require_owner)``:

- ``GET /api/finance/quotes``: orders still being quoted, with the latest
  quote version and the owner's decision on it.
- ``POST /api/finance/orders/{id}/quotes``: calculate and save the first quote.
- ``POST /api/finance/quotes/{id}/versions/{v}/approve``, ``/withhold`` and
  ``/revise``: act on exactly that version.

Customer routes check that the order is theirs; another customer's order is
"not found", the same as a missing one:

- ``GET /api/orders/{id}/quote``: the approved, current quote, or ``null``.
- ``POST /api/quotes/{id}/versions/{v}/accept`` and ``/reject``.

Money is always a decimal string, never a JSON number. The figures come only
from ``FinanceService``; until BAKE-22's explainer is wired in, a quote is
saved without an explanation and flagged for manual review (UC-05 4a).
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access import Identity, require_owner
from app.config import Settings
from app.db.session import get_sessionmaker
from app.pricing_bookkeeping.quotation import AdditionalCost
from app.pricing_bookkeeping.quotes import QueueItem, QuoteError, QuoteService, QuoteView
from app.sales.intake import CustomerIdentity
from app.sales.routes import current_customer
from app.shared.approvals import NotTheOwner
from app.shared.refs import RecordRef
from app.shared.store import StoreError, ValidatedRecordStore

STATUS_CODES = {"NOT_FOUND": 404, "VALIDATION_FAILED": 422}


class CostBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field(min_length=1, max_length=120)
    # A decimal string such as "12.50", never a JSON number.
    amount: str = Field(max_length=32)


class PricingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    additionalCosts: list[CostBody] = Field(default_factory=list, max_length=10)


def quote_json(view: QuoteView) -> dict:
    """The owner's view: every figure, the review flag and the explanation."""
    return {
        "id": str(view.ref.record_id),
        "version": view.ref.version,
        "orderId": str(view.order_id),
        "materialCost": str(view.material_cost),
        "labourCost": str(view.labour_cost),
        "packagingCost": str(view.packaging_cost),
        "additionalCost": str(view.additional_cost),
        "targetMargin": str(view.target_margin),
        "total": str(view.total),
        "customerDecision": view.customer_decision,
        "requiresManualReview": view.requires_manual_review,
        "explanation": view.explanation,
    }


def customer_quote_json(view: QuoteView) -> dict:
    """What the customer sees: the price to accept, not the bakery's costs."""
    return {
        "id": str(view.ref.record_id),
        "version": view.ref.version,
        "orderId": str(view.order_id),
        "total": str(view.total),
        "customerDecision": view.customer_decision,
    }


def queue_json(item: QueueItem) -> dict:
    return {
        "orderId": str(item.order_id),
        "summary": item.summary,
        "requiredAt": item.required_at.isoformat(),
        "orderStatus": item.order_status,
        "ready": item.ready,
        "approval": item.approval,
        "quote": quote_json(item.quote) if item.quote else None,
    }


def costs(body: PricingBody | None) -> list[AdditionalCost]:
    """Owner-entered extra costs; an amount that is not a decimal is a 422."""
    result = []
    for cost in body.additionalCosts if body else []:
        try:
            amount = Decimal(cost.amount)
        except InvalidOperation:
            raise HTTPException(422, f"{cost.description}: the amount must be a number such as 12.50") from None
        result.append(AdditionalCost(cost.description, amount))
    return result


def make_router(settings: Settings, *, store: ValidatedRecordStore | None = None) -> APIRouter:
    router = APIRouter()

    def quotes() -> QuoteService:
        actual_store = store
        if actual_store is None:
            if not settings.database_url:
                raise HTTPException(503, "Database not configured.")
            actual_store = ValidatedRecordStore(get_sessionmaker(settings.database_url))
        return QuoteService(actual_store)

    def run(action):
        try:
            return action()
        except QuoteError as exc:
            raise HTTPException(STATUS_CODES.get(exc.code, 409), exc.message) from None
        except NotTheOwner:
            raise HTTPException(403, "This operation requires the bakery owner.") from None
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "The quote could not be saved. Nothing was changed; please retry.") from None

    # Owner ----------------------------------------------------------------------------

    @router.get("/api/finance/quotes")
    def queue(owner: Identity = Depends(require_owner)):
        return {"orders": [queue_json(item) for item in run(lambda: quotes().queue())]}

    @router.post("/api/finance/orders/{order_id}/quotes")
    def draft(order_id: uuid.UUID, body: PricingBody | None = None,
              owner: Identity = Depends(require_owner)):
        extra = costs(body)
        view = run(lambda: quotes().draft(order_id, owner_id=owner.user_id, additional_costs=extra))
        return JSONResponse(status_code=201, content=quote_json(view))

    @router.post("/api/finance/quotes/{quote_id}/versions/{version}/revise")
    def revise(quote_id: uuid.UUID, version: int, body: PricingBody | None = None,
               owner: Identity = Depends(require_owner)):
        extra = costs(body)
        ref = RecordRef("Quote", quote_id, version)
        view = run(lambda: quotes().revise(ref, owner_id=owner.user_id, additional_costs=extra))
        return JSONResponse(status_code=201, content=quote_json(view))

    @router.post("/api/finance/quotes/{quote_id}/versions/{version}/approve")
    def approve(quote_id: uuid.UUID, version: int, owner: Identity = Depends(require_owner)):
        ref = RecordRef("Quote", quote_id, version)
        return quote_json(run(lambda: quotes().approve(ref, owner_id=owner.user_id)))

    @router.post("/api/finance/quotes/{quote_id}/versions/{version}/withhold")
    def withhold(quote_id: uuid.UUID, version: int, owner: Identity = Depends(require_owner)):
        ref = RecordRef("Quote", quote_id, version)
        return quote_json(run(lambda: quotes().withhold(ref, owner_id=owner.user_id)))

    # Customer -------------------------------------------------------------------------

    @router.get("/api/orders/{order_id}/quote")
    def customer_view(order_id: uuid.UUID, customer: CustomerIdentity = Depends(current_customer)):
        view = run(lambda: quotes().customer_view(order_id, customer_id=customer.customer_id))
        return {"quote": customer_quote_json(view) if view else None}

    @router.post("/api/quotes/{quote_id}/versions/{version}/accept")
    def accept(quote_id: uuid.UUID, version: int,
               customer: CustomerIdentity = Depends(current_customer)):
        ref = RecordRef("Quote", quote_id, version)
        accepted = run(lambda: quotes().accept(ref, customer_id=customer.customer_id))
        return {"orderId": str(accepted.order.record_id), "replayed": accepted.replayed}

    @router.post("/api/quotes/{quote_id}/versions/{version}/reject")
    def reject(quote_id: uuid.UUID, version: int,
               customer: CustomerIdentity = Depends(current_customer)):
        ref = RecordRef("Quote", quote_id, version)
        return customer_quote_json(run(lambda: quotes().reject(ref, customer_id=customer.customer_id)))

    return router
