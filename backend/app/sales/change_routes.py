"""Authenticated UC-02 boundaries; no success is returned before commit."""
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from app.auth.access import require_owner
from app.inventory_production import StockCheckError
from app.pricing_bookkeeping.quotation import QuotationInputError
from app.config import Settings
from app.db.session import get_sessionmaker
from app.llm.gateway import LLMGateway, LLMCallFailed
from app.sales.change_agent import mock_change
from app.sales.changes import ChangeService
from app.sales.intake import IntakeError
from app.sales.routes import current_customer, check_csrf
from app.shared.store import StoreError, ValidatedRecordStore
from app.workflow.order_flow import AIOperationFailed


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["Change", "Cancel", "Refund"]
    message: str = Field(min_length=1, max_length=2000)


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmationToken: str = Field(min_length=1, max_length=50000)


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class Decision(Version):
    decision: Literal["PrepareQuote", "ApproveQuote", "Clarify", "Reject"]
    note: str = Field(min_length=1, max_length=2000)


def make_router(settings: Settings, *, client_factory=None):
    router = APIRouter()
    secret = settings.summary_signing_secret()

    def service(message=None):
        if not settings.database_url:
            raise HTTPException(503, "Database not configured. No change was saved.")
        client = None
        if message is not None:
            client = client_factory(message) if client_factory else (mock_change(message) if settings.llm_mode == "mock" else LLMGateway.from_environment())
        return ChangeService(ValidatedRecordStore(get_sessionmaker(settings.database_url)), secret, client)

    def run(operation):
        try:
            return operation()
        except IntakeError as exc:
            raise HTTPException(404 if exc.code == "NOT_FOUND" else 409, exc.message) from None
        except (StockCheckError, QuotationInputError):
            raise HTTPException(409, "The proposed order could not be rechecked. Nothing was applied; ask the owner for assistance.") from None
        except (AIOperationFailed, LLMCallFailed):
            raise HTTPException(503, "We could not reliably interpret the change. Nothing was changed. Try one clear request or ask the owner for help.") from None
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "Saving was not confirmed. No partial order change was saved; retry the same confirmation.") from None

    @router.post("/api/orders/{order_id}/changes/preview", dependencies=[Depends(check_csrf)])
    def preview(order_id: uuid.UUID, body: Preview, customer=Depends(current_customer)):
        return run(lambda: service(body.message if body.kind == "Change" else None).preview(customer, order_id, body.kind, body.message))

    @router.post("/api/order-changes/confirm", dependencies=[Depends(check_csrf)])
    def confirm(body: Confirm, customer=Depends(current_customer)):
        return run(lambda: service().confirm(customer, body.confirmationToken))

    @router.get("/api/order-changes")
    def mine(customer=Depends(current_customer)):
        return run(lambda: {"requests": service().listing(customer)})

    @router.post("/api/order-changes/{request_id}/withdraw", dependencies=[Depends(check_csrf)])
    def withdraw(request_id: uuid.UUID, body: Version, customer=Depends(current_customer)):
        return run(lambda: service().withdraw(customer, request_id, body.version))

    @router.post("/api/order-changes/{request_id}/accept", dependencies=[Depends(check_csrf)])
    def accept(request_id: uuid.UUID, body: Version, customer=Depends(current_customer)):
        return run(lambda: service().accept(customer, request_id, body.version))

    @router.get("/api/owner/order-changes")
    def queue(owner=Depends(require_owner)):
        return run(lambda: {"requests": service().listing()})

    @router.post("/api/owner/order-changes/{request_id}/decision", dependencies=[Depends(check_csrf)])
    def decide(request_id: uuid.UUID, body: Decision, owner=Depends(require_owner)):
        return run(lambda: service().decide(owner.user_id, request_id, body.version, body.decision, body.note))

    return router
