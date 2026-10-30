"""Customer HTTP boundary for the local mock payment (BAKE-39 [O3]).

Customer only: an order and its payments are "not found" for anyone else. The
auth middleware checks the CSRF token on every POST. No route accepts an
amount: it always comes from the accepted quote, stored with the payment.

- ``POST /api/orders/{id}/payments`` starts a payment (201), or returns the
  open one (200).
- ``GET /api/orders/{id}/payments`` lists the order's payments.
- ``POST /api/payments/{id}/mock-checkout`` with ``{"outcome": "Succeeded"}``
  or ``"Failed"`` is the mock provider's checkout: the gateway reports the
  outcome for the stored amount, and the callback is settled once.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.session import get_sessionmaker
from app.pricing_bookkeeping.ledger import LedgerError
from app.pricing_bookkeeping.payments import (
    MockPaymentGateway,
    PaymentError,
    PaymentService,
    PaymentView,
)
from app.sales.intake import CustomerIdentity
from app.sales.routes import current_customer
from app.shared.store import StoreError, ValidatedRecordStore

STATUS_CODES = {"NOT_FOUND": 404, "VALIDATION_FAILED": 422}


class CheckoutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["Succeeded", "Failed"]


def payment_json(payment: PaymentView) -> dict:
    return {
        "id": str(payment.payment_id),
        "orderId": str(payment.order_id),
        "quoteId": str(payment.quote.record_id),
        "quoteVersion": payment.quote.version,
        # A decimal string, never a JSON number.
        "amount": str(payment.amount),
        "status": payment.status,
        "paid": payment.transaction_id is not None,
        "createdAt": payment.created_at.isoformat(),
        "processedAt": payment.processed_at.isoformat() if payment.processed_at else None,
    }


def make_router(
    settings: Settings,
    *,
    store: ValidatedRecordStore | None = None,
    gateway: MockPaymentGateway | None = None,
) -> APIRouter:
    router = APIRouter()
    gateway = gateway or MockPaymentGateway()

    def payments() -> PaymentService:
        actual_store = store
        if actual_store is None:
            if not settings.database_url:
                raise HTTPException(503, "Database not configured.")
            actual_store = ValidatedRecordStore(get_sessionmaker(settings.database_url))
        return PaymentService(actual_store)

    def refused(exc: PaymentError):
        raise HTTPException(STATUS_CODES.get(exc.code, 409), exc.message) from None

    def unavailable():
        raise HTTPException(
            503, "The payment could not be completed. Nothing was charged or recorded; please retry."
        ) from None

    @router.post("/api/orders/{order_id}/payments")
    def start(order_id: uuid.UUID, customer: CustomerIdentity = Depends(current_customer)):
        try:
            payment = payments().start(order_id, customer_id=customer.customer_id)
        except PaymentError as exc:
            refused(exc)
        except (StoreError, SQLAlchemyError):
            unavailable()
        return JSONResponse(
            status_code=200 if payment.replayed else 201,
            content=payment_json(payment) | {"replayed": payment.replayed},
        )

    @router.get("/api/orders/{order_id}/payments")
    def listed(order_id: uuid.UUID, customer: CustomerIdentity = Depends(current_customer)):
        try:
            found = payments().payments(order_id, customer_id=customer.customer_id)
        except PaymentError as exc:
            refused(exc)
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "Payments could not be loaded. Please try again.") from None
        return {"payments": [payment_json(payment) for payment in found]}

    @router.post("/api/payments/{payment_id}/mock-checkout")
    def checkout(
        payment_id: uuid.UUID,
        body: CheckoutBody,
        customer: CustomerIdentity = Depends(current_customer),
    ):
        try:
            service = payments()
            payment = service.payment_for_customer(payment_id, customer_id=customer.customer_id)
            settled = service.complete(gateway.checkout(payment, body.outcome))
        except PaymentError as exc:
            refused(exc)
        except (LedgerError, StoreError, SQLAlchemyError):
            unavailable()
        return payment_json(settled) | {"replayed": settled.replayed}

    return router
