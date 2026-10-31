"""UC-01 3c / UC-02 4c: audited owner review, never an implicit sale or refund."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.inventory_production import InventoryService
from app.sales.intake import CustomerIdentity, IntakeError, OrderIntake, OrderInterpretation, validate_order
from app.shared.approvals import ApprovalService
from app.shared.records import Customer, Order, OrderItem, OwnerReferral, Product, Recipe
from app.shared.store import RecordNotFound, StaleVersionError, ValidatedRecordStore

REVIEW_KIND = "OrderException"


class RecipeChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    productId: uuid.UUID
    recipeId: uuid.UUID
    recipeVersion: int = Field(ge=1)


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    summary: OrderInterpretation | None = None
    recipes: list[RecipeChoice] = Field(default_factory=list, max_length=30)
    note: str = Field(min_length=1, max_length=2000)


def needs_owner(summary: dict, text: str) -> bool:
    """Conservative routing only, not an allergen-safety classifier.

    Missing disclosure alone stays in ordinary clarification. Explicit concerns,
    uncertainty and recipe edits require a person even if extraction loses them.
    """
    if summary.get("allergen_concerns") or any(i.get("customisation") for i in summary.get("items", [])):
        return True
    cleaned = re.sub(r"\bno (?:known )?(?:allergies|allergy concerns)\b", "", text, flags=re.I)
    return bool(re.search(
        r"\ballerg\w*\b|\bintoleran\w*\b|\b(?:nut|peanut|dairy|gluten|egg)[ -]free\b|"
        r"\b(?:without|avoid|substitute|replace)\b|\bno (?:nuts|eggs|milk|dairy|gluten)\b", cleaned, re.I))


def view(row: OwnerReferral) -> dict:
    return {"id": str(row.referral_id), "version": row.version, "kind": row.kind,
            "status": row.status, "orderId": str(row.order_id) if row.order_id else None,
            "message": row.message, "summary": row.summary, "conversation": row.conversation,
            "resolution": row.resolution, "createdAt": row.created_at.isoformat()}


class ReferralService:
    def __init__(self, store: ValidatedRecordStore):
        self.store = store
        self.approvals = ApprovalService(store)

    def create(self, customer: CustomerIdentity, message: str, *, kind="Intake",
               summary: dict | None = None, order_id: uuid.UUID | None = None) -> dict:
        # Lock the account to make retries/concurrent identical submissions one request.
        key = hashlib.sha256(f"{kind}:{order_id}:{message}".encode()).hexdigest()
        with self.store.unit_of_work() as db:
            account = db.scalar(select(Customer).where(Customer.customer_id == customer.customer_id,
                                                       Customer.user_id == customer.user_id).with_for_update())
            if account is None:
                raise IntakeError("NOT_FOUND", "Request not found.")
            order = None
            if kind != "Intake":
                order = db.scalar(select(Order).where(Order.order_id == order_id,
                                                      Order.customer_id == customer.customer_id).with_for_update())
                if order is None:
                    raise IntakeError("NOT_FOUND", "Request not found.")
                if kind == "InProductionChange" and order.status != "InProduction":
                    raise IntakeError("CONFLICT", "This review is for work already in production.")
            if order is not None:
                key = hashlib.sha256(f"{key}:{order.version}:{order.status}".encode()).hexdigest()
            existing = db.scalar(select(OwnerReferral).where(OwnerReferral.customer_id == customer.customer_id,
                                                            OwnerReferral.request_key == key))
            if existing:
                return view(existing)
            row = OwnerReferral(customer_id=customer.customer_id, request_key=key, kind=kind,
                                message=message, summary=summary, order_id=order_id,
                                source_order_version=order.version if order else None,
                                source_order_status=order.status if order else None)
            self.store.commit(db, row, actor_id=customer.user_id, action="exception.requested")
            return view(row)

    def _load(self, db, request_id, version, customer=None):
        try:
            row = self.store.load(db, OwnerReferral, request_id, lock=True)
        except RecordNotFound:
            raise IntakeError("NOT_FOUND", "Request not found.") from None
        if customer is not None and row.customer_id != customer.customer_id:
            raise IntakeError("NOT_FOUND", "Request not found.")
        if row.version != version:
            raise StaleVersionError("This request changed. Refresh before continuing.")
        return row

    def _save(self, db, row, actor, action):
        self.store.commit(db, row, actor_id=actor, action=action, expected_version=row.version, revise=True)

    def ask(self, request_id, version, owner_id, question):
        with self.store.unit_of_work() as db:
            self.approvals._require_owner(db, owner_id)
            row = self._load(db, request_id, version)
            if row.status not in {"Pending", "AwaitingCustomer"}:
                raise IntakeError("CONFLICT", "This request cannot be changed now.")
            self._bounded(row)
            row.conversation = [*row.conversation, {"role": "Owner", "text": question}]
            row.status, row.resolution = "NeedsClarification", None
            self._save(db, row, owner_id, "exception.clarification_requested")
            return view(row)

    @staticmethod
    def _bounded(row):
        if len(row.conversation) >= 30:
            raise IntakeError("CONFLICT", "Please contact the bakery to continue this review.")

    def reply(self, request_id, version, customer, answer):
        with self.store.unit_of_work() as db:
            row = self._load(db, request_id, version, customer)
            if row.status not in {"NeedsClarification", "AwaitingCustomer"}:
                raise IntakeError("CONFLICT", "This request is not awaiting your response.")
            self._bounded(row)
            row.conversation = [*row.conversation, {"role": "Customer", "text": answer}]
            row.status, row.resolution = "Pending", None
            self._save(db, row, customer.user_id, "exception.customer_replied")
            return view(row)

    def _check_resolution(self, db, resolution):
        data = resolution.summary
        if data is None:
            raise IntakeError("VALIDATION_FAILED", "A complete order summary is required.")
        products = {p.name.casefold(): p.product_id for p in db.scalars(select(Product))}
        issues = validate_order(data, products, datetime.now(UTC))
        if issues:
            raise IntakeError("VALIDATION_FAILED", " ".join(issues))
        if len(resolution.recipes) != len(data.items):
            raise IntakeError("VALIDATION_FAILED", "Select a product and recipe for every item.")
        for item, choice in zip(data.items, resolution.recipes, strict=True):
            # Lock recipes so a concurrent recipe revision cannot pass the check
            # and then be used as if the old owner selection still applied.
            recipe = db.get(Recipe, choice.recipeId, with_for_update=True, populate_existing=True)
            if (recipe is None or recipe.product_id != choice.productId
                    or products.get(item.product.casefold()) != choice.productId
                    or recipe.version != choice.recipeVersion or not recipe.ingredients):
                raise IntakeError("VALIDATION_FAILED", "The selected recipe is missing, changed or does not match the product. Ask the owner to review again.")
        return data

    def _check_source(self, db, row):
        if row.kind == "Intake":
            return
        order = self.store.load(db, Order, row.order_id, lock=True)
        if order.version != row.source_order_version or order.status != row.source_order_status:
            raise IntakeError("CONFLICT", "The order changed during review. Submit a new request for its current state.")

    def resolve(self, request_id, version, owner_id, resolution):
        with self.store.unit_of_work() as db:
            self.approvals._require_owner(db, owner_id)
            row = self._load(db, request_id, version)
            if row.status != "Pending":
                raise IntakeError("CONFLICT", "Refresh or wait for the customer's clarification.")
            self._check_source(db, row)
            if row.kind == "Intake":
                self._check_resolution(db, resolution)
            elif resolution.summary is not None or resolution.recipes:
                raise IntakeError("VALIDATION_FAILED", "Record the proposed handling in the note; this review does not apply order or payment changes.")
            row.resolution = resolution.model_dump(mode="json")
            row.status = "AwaitingCustomer"
            self._save(db, row, owner_id, "exception.resolved")
            ref = self.store.ref(row)
            approval = self.approvals.request(db, ref, REVIEW_KIND, actor_id=owner_id)
            self.approvals.decide(db, approval, "Approved", owner_id=owner_id)
            return view(row)

    def confirm(self, request_id, version, customer):
        draft = None
        with self.store.unit_of_work() as db:
            # All confirmations lock the request first, including retries.
            row = self._load(db, request_id, version, customer)
            if row.status == "Completed":
                return view(row)
            if row.status != "AwaitingCustomer" or not self.approvals.is_approved(db, self.store.ref(row), REVIEW_KIND):
                raise IntakeError("CONFLICT", "The current resolution must be reviewed by the owner first.")
            self._check_source(db, row)
            resolution = Resolution.model_validate(row.resolution)
            if row.kind == "Intake":
                data = self._check_resolution(db, resolution)
                order = Order(customer_id=customer.customer_id, status="Draft", required_at=data.required_at,
                              collection_kind=data.collection_kind, budget=data.budget,
                              allergen_concerns=data.allergen_concerns,
                              items=[OrderItem(product_id=c.productId, resolved_recipe_id=c.recipeId,
                                               quantity=i.quantity, flavour=i.flavour, theme=i.theme,
                                               customisation=i.customisation)
                                     for i, c in zip(data.items, resolution.recipes, strict=True)])
                draft = self.store.commit(db, order, actor_id=customer.user_id, action="order.reviewed_draft_created")
                row.order_id = order.order_id
            # Refund/production-change confirmation only acknowledges the reviewed
            # proposal. Execution belongs to BAKE-30/ledger, not this review queue.
            row.status = "Completed"
            self.store.commit(db, row, actor_id=customer.user_id, action="exception.customer_confirmed",
                              expected_version=row.version)
            result = view(row)
        if draft:
            InventoryService(self.store).check_after_draft_saved(draft.record_id, order_version=draft.version,
                                                                actor_id=customer.user_id)
        return result


class ReferralIntake(OrderIntake):
    """Small integration seam compatible with BAKE-16's future preview arguments."""
    def preview(self, customer, message, *args, **kwargs):
        result = super().preview(customer, message, *args, **kwargs)
        answers = args[0] if args else kwargs.get("clarifications", [])
        text = "\n".join([message, *answers])
        if needs_owner(result["summary"], text):
            referral = ReferralService(self.store).create(customer, text, summary=result["summary"])
            result["confirmationToken"] = None
            result["issues"] = ["Your request is pending bakery owner review. No draft has been saved. See Owner reviews below."]
            result["ownerReview"] = referral
        return result
