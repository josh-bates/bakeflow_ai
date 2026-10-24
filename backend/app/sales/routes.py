"""UC-01 HTTP boundary. Authentication is supplied by the account integration."""
import secrets
import uuid
from app.auth.access import authenticated
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.session import get_sessionmaker
from app.llm.gateway import LLMGateway, MockLLMClient, LLMCallFailed
from app.sales.fixtures import mock_for
from app.sales.intake import CustomerIdentity, IntakeError, OrderIntake, conversation_text
from app.shared.records import Order, Product
from app.shared.store import StoreError, ValidatedRecordStore
from app.workflow.order_flow import AIOperationFailed


def current_customer(request: Request) -> CustomerIdentity:
    identity = authenticated(request)
    if identity.role != "Customer" or identity.customer_id is None:
        raise HTTPException(403, "A customer account is required.")
    return CustomerIdentity(identity.user_id, identity.customer_id, identity.display_name)


def check_csrf(request: Request):
    expected = getattr(request.state, "csrf_token", None)
    supplied = request.headers.get("x-csrf-token", "")
    if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(403, "Please refresh your session.")


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=4000)
    clarifications: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=8)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmationToken: str = Field(min_length=1, max_length=50000)


def make_router(settings: Settings, *, store: ValidatedRecordStore | None = None,
                client_factory: Callable | None = None) -> APIRouter:
    router = APIRouter()
    secret = settings.summary_signing_secret()

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

    @router.post("/api/order-requests", dependencies=[Depends(check_csrf)])
    def preview(body: PreviewBody, customer: CustomerIdentity = Depends(current_customer)):
        try:
            return service(conversation_text(body.message, body.clarifications)).preview(customer, body.message, body.clarifications)
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
    @router.get("/api/orders/{order_id}")
    def order(order_id: str, identity=Depends(authenticated)):
        try:
            key = uuid.UUID(order_id)
        except ValueError:
            raise HTTPException(404, "Order not found.") from None
        with service().store.unit_of_work() as db:
            query = select(Order).where(Order.order_id == key)
            if identity.role != "Owner":
                query = query.where(Order.customer_id == identity.customer_id)
            record = db.scalar(query)
            if record is None:
                raise HTTPException(404, "Order not found.")
            return {"id": str(record.order_id), "status": record.status,
                    "requiredAt": record.required_at.isoformat()}
    return router
