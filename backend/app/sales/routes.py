"""UC-01 HTTP boundary. Authentication is supplied by the account integration."""
import secrets
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.session import get_sessionmaker
from app.llm.gateway import LLMGateway, MockLLMClient, LLMCallFailed
from app.sales.fixtures import mock_for
from app.sales.intake import CustomerIdentity, IntakeError, OrderIntake
from app.shared.records import Order, Product
from app.shared.store import StoreError, ValidatedRecordStore
from app.workflow.order_flow import AIOperationFailed


def current_customer(request: Request) -> CustomerIdentity:
    # BAKE-14 can override this dependency after validating its server-side session.
    # No identity or role is accepted from request JSON or browser headers.
    raise HTTPException(401, "Please sign in.")


def check_csrf(request: Request):
    expected = getattr(request.state, "csrf_token", None)
    supplied = request.headers.get("x-csrf-token", "")
    if not expected or not secrets.compare_digest(expected, supplied):
        raise HTTPException(403, "Please refresh your session.")


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=4000)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmationToken: str = Field(min_length=1, max_length=50000)


def make_router(settings: Settings, *, store: ValidatedRecordStore | None = None,
                client_factory: Callable | None = None) -> APIRouter:
    router = APIRouter()
    secret = secrets.token_bytes(32)

    def service(message: str = "") -> OrderIntake:
        actual_store = store
        if actual_store is None:
            if not settings.database_url:
                raise HTTPException(503, "Database not configured.")
            actual_store = ValidatedRecordStore(get_sessionmaker(settings.database_url))
        client = MockLLMClient()
        if message:
            client = (client_factory(message) if client_factory else
                      mock_for(message) if settings.llm_mode == "mock" else LLMGateway.from_environment())
        return OrderIntake(actual_store, client, secret)

    def fail(exc: Exception):
        if isinstance(exc, AIOperationFailed):
            raise HTTPException(503, "We could not interpret your request after three attempts. No draft was saved.") from None
        if isinstance(exc, IntakeError):
            raise HTTPException(409, exc.message) from None
        raise HTTPException(503, "The request could not be completed. Saving was not confirmed; please retry.") from None

    @router.get("/api/session")
    def session(request: Request, customer: CustomerIdentity = Depends(current_customer)):
        return {"user": {"displayName": customer.name, "role": "Customer"},
                "mode": settings.llm_mode, "csrfToken": getattr(request.state, "csrf_token", "")}

    @router.post("/api/order-requests", dependencies=[Depends(check_csrf)])
    def preview(body: PreviewBody, customer: CustomerIdentity = Depends(current_customer)):
        try:
            return service(body.message).preview(customer, body.message)
        except (AIOperationFailed, LLMCallFailed, IntakeError, StoreError, SQLAlchemyError) as exc:
            fail(exc)

    @router.post("/api/order-requests/confirm", dependencies=[Depends(check_csrf)])
    def confirm(body: ConfirmBody, customer: CustomerIdentity = Depends(current_customer)):
        try:
            return service().confirm(customer, body.confirmationToken)
        except (IntakeError, StoreError, SQLAlchemyError) as exc:
            fail(exc)

    @router.get("/api/orders/mine")
    def orders(customer: CustomerIdentity = Depends(current_customer)):
        try:
            with service().store.unit_of_work() as db:
                products = {p.product_id: p.name for p in db.scalars(select(Product))}
                records = db.scalars(select(Order).where(Order.customer_id == customer.customer_id)
                                     .order_by(Order.created_at.desc())).all()
                return {"orders": [{"id": str(o.order_id), "status": o.status,
                                    "summary": ", ".join(f"{i.quantity} {products[i.product_id]}" for i in o.items),
                                    "requiredAt": o.required_at.isoformat()} for o in records]}
        except (StoreError, SQLAlchemyError) as exc:
            fail(exc)
    return router
