"""UC-06 ledger and profitability HTTP boundary (BAKE-24, BAKE-34). Owner only.

``/api/finance`` is an owner-only route family: BAKE-14's middleware checks the
session, the owner role and the CSRF token for every request under it. Each
handler also declares ``Depends(require_owner)``, as BAKE-14 asks of owner
handlers, and records the signed-in owner as the transaction's actor.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access import Identity, require_owner
from app.config import Settings
from app.db.session import get_sessionmaker
from app.llm.gateway import ILLMClient, LLMCallFailed, LLMGateway
from app.pricing_bookkeeping.ledger import (
    DuplicateTransaction,
    LedgerEntry,
    LedgerService,
    RequestKeyConflict,
    TransactionInput,
    TransactionRejected,
)
from app.pricing_bookkeeping.fixtures import mock_profit_explainer
from app.pricing_bookkeeping.profit_explanation import ProfitExplainer, profit_figures
from app.pricing_bookkeeping.profitability import ProfitabilityService, ProfitabilitySummary, ProfitLine
from app.pricing_bookkeeping.quotation import ValidationIssue
from app.shared.store import StoreError, ValidatedRecordStore

logger = logging.getLogger(__name__)


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


def line_json(line: ProfitLine) -> dict:
    """Money as decimal strings, never JSON numbers; no margin without income."""
    return {
        "income": str(line.income),
        "expenses": str(line.expenses),
        "profit": str(line.profit),
        "margin": str(line.margin) if line.margin is not None else None,
        "transactions": line.transactions,
    }


def check_period(start: datetime | None, end: datetime | None) -> None:
    for name, bound in (("start", start), ("end", end)):
        if bound is not None and bound.utcoffset() is None:
            raise HTTPException(422, f"{name} needs a time zone, e.g. 2026-10-01T00:00:00+10:00")
    if start is not None and end is not None and end <= start:
        raise HTTPException(422, "end must be after start")


def profitability_json(summary: ProfitabilitySummary, start: datetime | None, end: datetime | None) -> dict:
    return {
        "overall": line_json(summary.overall),
        # Shown next to the per-order list: ingredient invoices are usually
        # general, so per-order margins alone overstate profit (review of #20).
        "general": line_json(summary.general),
        "byOrder": [{"orderId": str(o.order_id), **line_json(o.line)} for o in summary.by_order],
        "byProduct": [
            {"productId": str(p.product_id), "name": p.name, **line_json(p.line)}
            for p in summary.by_product
        ],
        "periodStart": start.isoformat() if start else None,
        "periodEnd": end.isoformat() if end else None,
    }


@dataclass(frozen=True)
class _NoProvider:
    """Stands in when the live provider is not configured: every attempt fails (5a)."""

    reason: str

    def complete(self, prompt: str) -> str:
        raise LLMCallFailed(self.reason)


def make_router(
    settings: Settings,
    *,
    store: ValidatedRecordStore | None = None,
    client_factory: Callable[[dict, dict], ILLMClient] | None = None,
) -> APIRouter:
    router = APIRouter()

    def llm_client(figures: dict, names: dict) -> ILLMClient:
        """The BAKE-34 explainer's model client: Mock, live, or one that always fails (5a)."""
        if client_factory is not None:
            return client_factory(figures, names)
        if settings.llm_mode == "mock":
            return mock_profit_explainer(figures, names)
        try:
            return LLMGateway.from_environment()
        except (LLMCallFailed, ValueError) as exc:
            return _NoProvider(str(exc))

    def ledger() -> LedgerService:
        actual_store = store
        if actual_store is None:
            if not settings.database_url:
                raise HTTPException(503, "Database not configured.")
            actual_store = ValidatedRecordStore(get_sessionmaker(settings.database_url))
        return LedgerService(actual_store)

    def profitability_service() -> ProfitabilityService:
        return ProfitabilityService(ledger().store)

    @router.post("/api/finance/transactions")
    def record(body: TransactionBody, owner: Identity = Depends(require_owner)):
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
    def transactions(owner: Identity = Depends(require_owner)):
        try:
            return {"transactions": [entry_json(entry) for entry in ledger().transactions()]}
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "The ledger could not be loaded; please retry.") from None

    @router.get("/api/finance/profitability")
    def profitability(
        start: datetime | None = None,
        end: datetime | None = None,
        owner: Identity = Depends(require_owner),
    ):
        """UC-06 step 4: profit overall, per order and per product (BAKE-34).

        ``start`` / ``end`` are optional ISO times with a time zone; the period
        includes its start and excludes its end.
        """
        check_period(start, end)
        try:
            summary = profitability_service().summary(period_start=start, period_end=end)
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "Profitability could not be loaded; please retry.") from None
        return profitability_json(summary, start, end)

    @router.post("/api/finance/profitability/explanation")
    def explain_profitability(
        start: datetime | None = None,
        end: datetime | None = None,
        owner: Identity = Depends(require_owner),
    ):
        """UC-06 steps 5-7: the same figures, with an explanation when one passes the check.

        Only on request, so the Finance page never waits on a model. If no
        explanation is usable (5a), the figures come back unchanged with a notice.
        """
        check_period(start, end)
        try:
            summary = profitability_service().summary(period_start=start, period_end=end)
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "Profitability could not be loaded; please retry.") from None
        explained = ProfitExplainer(llm_client(*profit_figures(summary))).explain(
            summary, actor=str(owner.user_id)
        )
        if explained.text is None:
            logger.warning("No usable profit explanation: %s", "; ".join(explained.problems))
        return profitability_json(summary, start, end) | {
            "explanation": explained.text,
            "notice": explained.notice or None,
        }

    return router
