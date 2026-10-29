"""Inventory and production HTTP boundary (BAKE-31, BAKE-32, BAKE-21, BAKE-37). Owner only.

``/api/inventory`` and ``/api/schedules`` are owner-only route families in the
auth middleware, which also checks the CSRF token on every POST; each handler
also declares ``require_owner``.

- A shopping-list explanation that fails is still a 200 response with the
  saved list and the unavailable notice (UC-03 6a).
- UC-03 7a: the owner records a delivered ingredient (201), and a repeat of the
  same receipt returns it again (200) without raising stock twice. Stock
  levels list what is on hand, committed to released plans and available.
  Quantities are decimal strings both ways, never JSON numbers.
- UC-04: the owner asks for a plan or a revised plan for some days (BAKE-20
  proposes it, in Mock mode with ``GreedyMockPlanner``), lists the plans with
  their derived state and approves one exact version, or withholds approval
  for now (BAKE-21).
  A release that fails its final check is a 200 ``ReleaseBlocked`` with the
  reasons: the owner's decision is recorded, but nothing was released.
- BAKE-37 [O1]: stock levels also give each ingredient's reorder level and,
  when it is low, the suggested reorder; reading them never calls a model.
  The owner changes a reorder level for the version they saw (409 if it
  changed), and asks for an explanation of the alerts, which, like UC-03 6a,
  is still a 200 with the alerts and a notice when the AI fails.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access import Identity, require_owner
from app.config import Settings
from app.db.session import get_sessionmaker
from app.inventory_production.fixtures import (
    GreedyMockPlanner,
    MockLowStockExplainer,
    MockShortageExplainer,
)
from app.inventory_production.inventory import (
    InventoryService,
    ReceiptConflict,
    ReceiptInput,
    ReceiptRejected,
    ReorderLevelRejected,
    StockLevel,
    StockReceipt,
)
from app.inventory_production.low_stock import LowStockExplainer
from app.inventory_production.release import ScheduleRelease, ScheduleView, VersionView
from app.inventory_production.scheduling import PlanOutcome, PlanStatus, ProductionPlanner
from app.inventory_production.shortages import ShortageExplainer
from app.llm.gateway import LLMCallFailed, LLMGateway
from app.shared import AlreadyDecided, ApprovalNotRequested, NotTheOwner
from app.shared.store import RecordNotFound, StaleVersionError, StoreError, ValidatedRecordStore

SOURCE_LABELS = {"Invoice": "Invoice", "DeliveryNote": "Delivery note"}

# Keeps the planning request, and the agent's reply, to a reviewable size.
MAX_PLAN_DAYS = 14


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


class ReorderLevelBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    # A decimal string in the item's stock unit, such as "200"; never a JSON number.
    reorderLevel: str = Field(max_length=32)
    # The item version the owner saw; a newer one is refused.
    version: int = Field(ge=1)


def level_json(level: StockLevel) -> dict:
    return {
        "id": str(level.item_id),
        "ingredient": level.ingredient,
        "unit": level.unit,
        "onHand": str(level.on_hand),
        "committed": str(level.committed),
        "available": str(level.available),
        "reorderLevel": str(level.reorder_level),
        "version": level.version,
        "low": level.low,
        "suggestedReorder": None if level.suggested_reorder is None else str(level.suggested_reorder),
    }


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


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    startDate: date
    endDate: date


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The exact version the owner reviewed; a newer one is refused.
    version: int = Field(ge=1)
    # Withheld leaves the version pending (UC-04 7b); it records no decision.
    decision: Literal["Approved", "Withheld"]


def plan_json(outcome: PlanOutcome) -> dict:
    return {
        "status": outcome.status,
        "schedule": outcome.schedule_ref.to_json() if outcome.schedule_ref else None,
        "rationale": outcome.rationale or None,
        "confidence": outcome.confidence,
        "problems": list(outcome.problems),
        "excluded": [
            {"orderId": str(e.order_ref.record_id), "reason": e.reason} for e in outcome.excluded
        ],
    }


def version_json(version: VersionView, zone: ZoneInfo) -> dict:
    """Task times in the bakery's time zone, so the browser shows them as given."""
    return {
        "version": version.version,
        "startDate": version.start_date.isoformat(),
        "endDate": version.end_date.isoformat(),
        "tasks": [
            {
                "id": str(t.task_id),
                "orderId": str(t.order_id),
                "kind": t.task_kind,
                "startAt": t.start_at.astimezone(zone).isoformat(),
                "endAt": t.end_at.astimezone(zone).isoformat(),
                "status": t.status,
            }
            for t in version.tasks
        ],
    }


def schedule_json(view: ScheduleView, zone: ZoneInfo) -> dict:
    return {
        "id": str(view.schedule_ref.record_id),
        "version": view.schedule_ref.version,
        "state": view.state,
        "releasedVersion": view.released_version,
        "notice": view.notice or None,
        "latest": version_json(view.latest, zone),
        "released": version_json(view.released, zone) if view.released else None,
    }


def _check_days(body: PlanBody) -> None:
    if body.endDate < body.startDate:
        raise HTTPException(422, "The plan's last day is before its first day.")
    if (body.endDate - body.startDate).days >= MAX_PLAN_DAYS:
        raise HTTPException(422, f"A plan covers at most {MAX_PLAN_DAYS} days.")


def make_router(settings: Settings, *, store: ValidatedRecordStore | None = None,
                client_factory: Callable | None = None,
                plan_client_factory: Callable | None = None,
                low_stock_client_factory: Callable | None = None) -> APIRouter:
    router = APIRouter()

    def actual_store() -> ValidatedRecordStore:
        if store is not None:
            return store
        if not settings.database_url:
            raise HTTPException(503, "Database not configured.")
        return ValidatedRecordStore(get_sessionmaker(settings.database_url))

    def model_client(factory: Callable | None, mock: Callable):
        if factory is not None:
            return factory()
        if settings.llm_mode == "mock":
            return mock()
        try:
            return LLMGateway.from_environment()
        except (LLMCallFailed, ValueError) as exc:
            # Missing or unusable settings, such as a URL urlsplit cannot read.
            return _NoProvider(str(exc))

    def explainer() -> ShortageExplainer:
        return ShortageExplainer(actual_store(), model_client(client_factory, MockShortageExplainer))

    def planner() -> ProductionPlanner:
        return ProductionPlanner(actual_store(), model_client(plan_client_factory, GreedyMockPlanner))

    def low_stock_explainer() -> LowStockExplainer:
        return LowStockExplainer(actual_store(), model_client(low_stock_client_factory, MockLowStockExplainer))

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
        return {"items": [level_json(level) for level in levels]}

    @router.put("/api/inventory/items/{item_id}/reorder-level")
    def set_reorder_level(item_id: uuid.UUID, body: ReorderLevelBody, owner: Identity = Depends(require_owner)):
        try:
            level = Decimal(body.reorderLevel)
        except InvalidOperation:
            level = body.reorderLevel  # reported by validation as "must be a number"
        try:
            saved = InventoryService(actual_store()).set_reorder_level(
                item_id, level, expected_version=body.version, actor_id=owner.user_id
            )
        except ReorderLevelRejected as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": "The reorder level needs fixing. Nothing was changed.",
                         "issues": [{"field": "reorderLevel", "message": str(exc)}]},
            )
        except RecordNotFound:
            raise HTTPException(404, "Ingredient not found.") from None
        except StaleVersionError:
            raise HTTPException(
                409, "This ingredient changed since you loaded it. Reload to see its current stock."
            ) from None
        except (StoreError, SQLAlchemyError):
            raise HTTPException(
                503, "The reorder level could not be saved. Nothing was changed; please retry."
            ) from None
        return level_json(saved)

    @router.post("/api/inventory/low-stock/explanation")
    def explain_low_stock(owner: Identity = Depends(require_owner)):
        try:
            outcome = low_stock_explainer().explain(actor_id=owner.user_id)
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "Stock levels could not be loaded. Please try again.") from None
        return {
            "status": outcome.status,
            "explanation": outcome.text or None,
            "notice": outcome.notice or None,
            "items": [level_json(level) for level in outcome.lines],
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

    @router.get("/api/schedules")
    def schedules(owner: Identity = Depends(require_owner)):
        try:
            zone, views = ScheduleRelease(actual_store()).schedules()
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "The production plans could not be loaded. Please try again.") from None
        return {"timeZone": zone, "schedules": [schedule_json(v, ZoneInfo(zone)) for v in views]}

    def propose(body: PlanBody, owner: Identity, schedule_id: uuid.UUID | None) -> JSONResponse:
        _check_days(body)
        try:
            outcome = planner().propose(
                body.startDate, body.endDate, actor_id=owner.user_id, schedule_id=schedule_id
            )
        except RecordNotFound:
            raise HTTPException(404, "Plan not found.") from None
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "The plan could not be prepared. Nothing was saved; please retry.") from None
        status = 201 if outcome.status is PlanStatus.PROPOSED else 200
        return JSONResponse(status_code=status, content=plan_json(outcome))

    @router.post("/api/schedules")
    def new_plan(body: PlanBody, owner: Identity = Depends(require_owner)):
        return propose(body, owner, None)

    @router.post("/api/schedules/{schedule_id}/revisions")
    def revise(schedule_id: uuid.UUID, body: PlanBody, owner: Identity = Depends(require_owner)):
        return propose(body, owner, schedule_id)

    @router.post("/api/schedules/{schedule_id}/decision")
    def decide(schedule_id: uuid.UUID, body: DecisionBody, owner: Identity = Depends(require_owner)):
        release = ScheduleRelease(actual_store())
        action = release.approve if body.decision == "Approved" else release.withhold
        try:
            outcome = action(schedule_id, body.version, owner_id=owner.user_id)
        except RecordNotFound:
            raise HTTPException(404, "Plan not found.") from None
        except StaleVersionError:
            raise HTTPException(409, "This plan has a newer version. Review the latest version.") from None
        except AlreadyDecided:
            raise HTTPException(
                409, "This version already has your decision. Request a revised plan to decide again."
            ) from None
        except ApprovalNotRequested:
            raise HTTPException(409, "This version has no approval request. Request a revised plan.") from None
        except NotTheOwner:
            raise HTTPException(403, "This operation requires the bakery owner.") from None
        except (StoreError, SQLAlchemyError):
            raise HTTPException(
                503, "The decision could not be saved. Nothing was released; please retry."
            ) from None
        return {
            "status": outcome.status,
            "schedule": outcome.schedule_ref.to_json(),
            "problems": list(outcome.problems),
        }

    return router
