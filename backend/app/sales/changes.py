"""UC-02: preview, explicit consent, guarded application or owner review."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from app.inventory_production import InventoryService
from app.pricing_bookkeeping.quotation import AdditionalCost, CostingInputs, FinanceService, IngredientRequirement, QuoteLine
from app.sales.change_agent import ChangeSalesAgent
from app.sales.intake import CustomerIdentity, IntakeError, SummaryTokens
from app.shared.approvals import ApprovalService, ApprovalKind
from app.shared.records import (
    AuditEntry, ApprovalRequest, BakerySettings, CustomerDecision, InventoryItem,
    MockPayment, Order, OrderChangeRequest, OrderDeduction, OrderItem, Product,
    ProductionTask, Quote, ShoppingList, Transaction,
)
from app.shared.refs import RecordRef
from app.shared.store import ValidatedRecordStore
from app.workflow.order_flow import WorkflowService, OrderStage

ACTIVE = ("Pending", "AwaitingApproval", "Quoted")


def pending_change(db, order_id) -> bool:
    return db.scalar(select(OrderChangeRequest.change_id).where(
        OrderChangeRequest.order_id == order_id, OrderChangeRequest.review_state.in_(ACTIVE)).limit(1)) is not None


def only_closed_amendments_after(db, quote) -> bool:
    """An abandoned amendment does not replace the original accepted contract."""
    newer = db.scalars(select(Quote).where(Quote.quote_id == quote.quote_id, Quote.version > quote.version)).all()
    if not newer:
        return False
    for candidate in newer:
        row = db.get(OrderChangeRequest, candidate.pending_change_id) if candidate.pending_change_id else None
        if row is None or row.review_state not in ("Rejected", "Withdrawn"):
            return False
    return True


def order_stamp(db, order):
    audit = db.scalar(select(AuditEntry.audit_id).where(AuditEntry.subject_type == "Order", AuditEntry.subject_id == order.order_id)
                      .order_by(AuditEntry.created_at.desc(), AuditEntry.audit_id.desc()).limit(1))
    return {"version": order.version, "status": order.status, "audit": str(audit)}


def environment_stamp(db, order):
    # Include decisions and work progression, which need not change Order.version.
    quotes = db.scalars(select(Quote).where(Quote.order_id == order.order_id).order_by(Quote.quote_id, Quote.version)).all()
    tasks = db.scalars(select(ProductionTask).where(ProductionTask.order_id == order.order_id).order_by(ProductionTask.task_id)).all()
    decisions = db.scalars(select(ApprovalRequest).where(ApprovalRequest.subject_id.in_([q.quote_id for q in quotes]))
                          .order_by(ApprovalRequest.request_id)).all()
    payments = db.scalars(select(MockPayment).where(MockPayment.order_id == order.order_id).order_by(MockPayment.payment_id)).all()
    return {"order": order_stamp(db, order),
            "quotes": [[str(q.quote_id), q.version, q.customer_decision] for q in quotes],
            "decisions": [[str(a.request_id), a.decision, str(a.invalidated_at)] for a in decisions],
            "tasks": [[str(t.task_id), t.status, t.lock_version] for t in tasks],
            "payments": [[str(p.payment_id), p.status] for p in payments]}


def work_started(db, order):
    return (order.status in ("InProduction", "Completed")
            or db.scalar(select(ProductionTask.task_id).where(ProductionTask.order_id == order.order_id, ProductionTask.status != "Planned").limit(1)) is not None
            or db.scalar(select(OrderDeduction.deduction_id).where(OrderDeduction.order_id == order.order_id)) is not None)


def has_payment(db, order):
    return (db.scalar(select(MockPayment.payment_id).where(MockPayment.order_id == order.order_id, MockPayment.status.in_(["Created", "Succeeded"])).limit(1)) is not None
            or db.scalar(select(Transaction.transaction_id).where(Transaction.order_id == order.order_id, Transaction.kind == "Income").limit(1)) is not None)


class ChangeTokens(SummaryTokens):
    def issue_change(self, customer, payload):
        payload = payload | {"purpose": "order-change-v1", "customer": str(customer.customer_id), "user": str(customer.user_id),
                             "id": str(uuid.uuid4()), "expires": int(time.time()) + 1800}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
        return body + "." + hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()


class ChangeService(WorkflowService):
    def __init__(self, store: ValidatedRecordStore, secret: bytes, client=None):
        super().__init__()
        self.store, self.tokens, self.client = store, ChangeTokens(secret), client
        self.approvals = ApprovalService(store)

    def _order(self, db, order_id, customer=None, lock=False):
        query = select(Order).where(Order.order_id == order_id)
        if customer is not None:
            query = query.where(Order.customer_id == customer.customer_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        order = db.scalar(query)
        if order is None:
            raise IntakeError("NOT_FOUND", "Order not found.")
        return order

    def _route(self, db, order, kind, terms):
        if kind != "Refund" and order.status in ("Cancelled", "Completed"):
            raise IntakeError("NOT_PERMITTED", "This order cannot be changed or cancelled. Contact the bakery about any refund request.")
        if kind == "Refund" or terms.get("scope") == "OwnerReview" or work_started(db, order) or has_payment(db, order):
            return "OwnerReview"
        if kind == "Change" and order.status in ("Confirmed", "Scheduled"):
            return "Requote"
        return "Apply"

    @staticmethod
    def _validate(order, terms):
        if terms.get("scope") == "Quantity":
            if order.required_at <= datetime.now(UTC):
                raise IntakeError("VALIDATION_FAILED", "The collection time has passed; ask the owner to review this order.")
            if len(order.items) != 1:
                raise IntakeError("VALIDATION_FAILED", "This order has multiple items. Ask the owner which item should change.")
            if order.allergen_concerns or any(i.customisation not in (None, "", "standard") for i in order.items):
                raise IntakeError("VALIDATION_FAILED", "This custom recipe needs owner review. Describe the recipe change instead.")
            if order.items[0].quantity == terms["quantity"]:
                raise IntakeError("VALIDATION_FAILED", "That quantity is already recorded; nothing needs changing.")
        if terms.get("scope") == "Collection":
            when = datetime.fromisoformat(terms["required_at"])
            if when <= datetime.now(UTC):
                raise IntakeError("VALIDATION_FAILED", "Choose a future collection time.")
            if when == order.required_at and terms.get("collection_kind", order.collection_kind) == order.collection_kind:
                raise IntakeError("VALIDATION_FAILED", "That collection time is already recorded.")

    def preview(self, customer: CustomerIdentity, order_id, kind, message):
        with self.store.snapshot() as db:
            order = self._order(db, order_id, customer)
            if pending_change(db, order_id):
                raise IntakeError("CONFLICT", "An order request is already pending. Withdraw it before starting another.")
            stamp = environment_stamp(db, order)
            ref, stage = self.store.ref(order), OrderStage(order.status)
        terms = {}
        if kind == "Change":
            result = self.run_ai_task(ref, stage, lambda: ChangeSalesAgent(self.client).interpret(message),
                                      lambda result: True, actor=str(customer.user_id), event_type="interpretOrderChange")
            terms = result.model_dump(mode="json")
            collection_kind = re.search(r"\bcollection (at|before)\b", message, re.I)
            if result.scope == "Collection" and collection_kind:
                terms["collection_kind"] = collection_kind[1].lower()
            # Safety concerns cannot be lost even if the model suggests a quantity.
            if re.search(r"allerg|recipe|gluten|dairy|without|refund|cancel|ingredient", message, re.I):
                terms = {"scope": "OwnerReview", "evidence": message}
        with self.store.snapshot() as db:
            order = self._order(db, order_id, customer)
            if environment_stamp(db, order) != stamp:
                raise IntakeError("STALE_VERSION", "The order changed. Review a fresh request.")
            self._validate(order, terms)
            route = self._route(db, order, kind, terms)
            current = {"status": order.status, "quantity": order.items[0].quantity if len(order.items) == 1 else None,
                       "requiredAt": order.required_at.isoformat(), "collectionKind": order.collection_kind}
        outcome = {"Apply": "Cancel the order. No refund or ingredient reversal is performed." if kind == "Cancel" else
                   "Save these changes and require a fresh stock check and owner-approved quotation.",
                   "OwnerReview": "Send this request for owner review. The order and any payment stay unchanged; affected production pauses.",
                   "Requote": "Request revised terms. Original terms stay in force until you accept a newly approved quotation; affected production pauses."}[route]
        payload = {"orderId": str(order_id), "kind": kind, "message": message, "terms": terms, "stamp": stamp, "route": route}
        return {"current": current, "proposed": terms, "outcome": outcome, "route": route,
                "confirmationToken": self.tokens.issue_change(customer, payload)}

    def confirm(self, customer, token):
        payload = self.tokens.read(token, customer)
        if payload.get("purpose") != "order-change-v1":
            raise IntakeError("SUMMARY_EXPIRED", "Review a new order-change summary.")
        with self.store.constraint_guard() as db:
            order = self._order(db, uuid.UUID(payload["orderId"]), customer, lock=True)
            previous = db.get(OrderChangeRequest, uuid.UUID(payload["id"]))
            if previous is not None:
                if previous.review_state == "Withdrawn":
                    raise IntakeError("CONFLICT", "This request was withdrawn.")
                return self.view(db, previous)
            if environment_stamp(db, order) != payload["stamp"]:
                raise IntakeError("STALE_VERSION", "The order, quotation or work changed. Nothing was applied; review a fresh request.")
            if pending_change(db, order.order_id):
                raise IntakeError("CONFLICT", "Another request is already pending.")
            self._validate(order, payload["terms"])
            route = self._route(db, order, payload["kind"], payload["terms"])
            if route != payload["route"]:
                raise IntakeError("STALE_VERSION", "The permitted outcome changed. Review a new summary.")
            row = OrderChangeRequest(change_id=uuid.UUID(payload["id"]), order_id=order.order_id,
                actor_id=customer.user_id, expected_order_version=order.version, expected_state=payload["stamp"]["order"],
                kind=payload["kind"], message=payload["message"], proposed_terms=payload["terms"] | {"route": route},
                review_state="Pending")
            if route == "Apply":
                if row.kind == "Cancel":
                    order.status = self.move_order(self.store.ref(order), OrderStage(order.status), OrderStage.CANCELLED,
                                                  actor=str(customer.user_id), event_type="cancelOrder").value
                else:
                    self._apply_terms(order, row.proposed_terms)
                self.store.commit(db, order, actor_id=customer.user_id, action="order.cancelled" if row.kind == "Cancel" else "order.changed",
                                  expected_version=order.version, revise=True)
                if row.kind == "Change":
                    inventory = InventoryService(self.store)
                    needs = inventory.calculate_needs(db, order)
                    shortages = inventory.check_stock(db, needs, exclude_orders={order.order_id})
                    self.store.commit(db, ShoppingList(order_id=order.order_id, source_order_version=order.version,
                        requirements={str(k): str(v) for k, v in needs.items()}, shortages={str(k): str(v) for k, v in shortages.items()}),
                        actor_id=customer.user_id, action="order_change.stock_rechecked")
                row.review_state, row.applied_at = "Applied", datetime.now(UTC)
                self._invalidate_quotes(db, order, customer.user_id)
            if route != "Apply" and order.status in ("Draft", "Quoted"):
                self._invalidate_quotes(db, order, customer.user_id)
            self._invalidate_plans(db, order, customer.user_id)
            self.store.commit(db, row, actor_id=customer.user_id, action="order_change.confirmed")
            return self.view(db, row)

    @staticmethod
    def _apply_terms(order, terms):
        if terms.get("scope") == "Quantity":
            order.items[0].quantity = terms["quantity"]
        elif terms.get("scope") == "Collection":
            order.required_at = datetime.fromisoformat(terms["required_at"])
            order.collection_kind = terms.get("collection_kind", order.collection_kind)

    def _invalidate_quotes(self, db, order, actor):
        for q in db.scalars(select(Quote).where(Quote.order_id == order.order_id)):
            self.approvals.invalidate(db, RecordRef("Quote", q.quote_id, q.version), ApprovalKind.QUOTATION,
                                      "Customer confirmed an order change; recheck and approve a new quote.", actor_id=actor)

    def _invalidate_plans(self, db, order, actor):
        ids = db.scalars(select(ProductionTask.schedule_id).where(ProductionTask.order_id == order.order_id).distinct()).all()
        for approval in db.scalars(select(ApprovalRequest).where(ApprovalRequest.subject_type == "ProductionSchedule",
                                                                 ApprovalRequest.subject_id.in_(ids))):
            self.approvals.invalidate(db, RecordRef("ProductionSchedule", approval.subject_id, approval.subject_version),
                                      ApprovalKind.SCHEDULE, "An order change/cancellation requires plan rechecking.", actor_id=actor)

    def _request(self, db, request_id, customer=None):
        row = db.get(OrderChangeRequest, request_id)
        if row is None:
            raise IntakeError("NOT_FOUND", "Request not found.")
        order = self._order(db, row.order_id, customer, lock=True)
        row = self.store.load(db, OrderChangeRequest, request_id, lock=True)
        return row, order

    def _original(self, db, row, order):
        if order_stamp(db, order) != row.expected_state:
            raise IntakeError("STALE_VERSION", "The order moved on. Reject or withdraw this request and start again.")
        if work_started(db, order) or has_payment(db, order):
            raise IntakeError("OWNER_REVIEW", "Started work or payment needs a separate owner decision; no terms were applied.")
        self._validate(order, row.proposed_terms)

    def decide(self, owner_id, request_id, version, decision, note):
        with self.store.constraint_guard() as db:
            self.approvals._require_owner(db, owner_id)
            row, order = self._request(db, request_id)
            if row.version != version:
                raise IntakeError("STALE_VERSION", "Refresh this request before deciding.")
            if row.review_state not in ACTIVE:
                raise IntakeError("CONFLICT", "This request is already closed.")
            if decision == "Reject":
                if row.quote_id:
                    self.approvals.invalidate(db, RecordRef("Quote", row.quote_id, row.quote_version), ApprovalKind.QUOTATION,
                                              "Order amendment rejected.", actor_id=owner_id)
                row.review_state = "Rejected"
            elif decision == "PrepareQuote":
                if row.proposed_terms.get("route") != "Requote" or row.review_state != "Pending":
                    raise IntakeError("OWNER_REVIEW", "This exception needs owner clarification; it cannot be automatically repriced.")
                self._original(db, row, order)
                quote = self._prepare_quote(db, row, order, owner_id)
                row.quote_id, row.quote_version, row.review_state = quote.quote_id, quote.version, "AwaitingApproval"
            elif decision == "ApproveQuote":
                self._original(db, row, order)
                if row.review_state != "AwaitingApproval":
                    raise IntakeError("CONFLICT", "Prepare a quotation before approving it.")
                ref = RecordRef("Quote", row.quote_id, row.quote_version)
                request = self.approvals.request(db, ref, ApprovalKind.QUOTATION, actor_id=owner_id)
                self.approvals.decide(db, request, "Approved", owner_id=owner_id)
                row.review_state = "Quoted"
            elif decision == "Clarify":
                if row.quote_id:
                    self.approvals.invalidate(db, RecordRef("Quote", row.quote_id, row.quote_version), ApprovalKind.QUOTATION,
                                              "Owner requested clarification; quotation needs renewed review.", actor_id=owner_id)
                row.review_state = "Pending"
            else:
                raise IntakeError("VALIDATION_FAILED", "Unknown review decision.")
            row.owner_note = note
            self.store.commit(db, row, actor_id=owner_id, action="order_change.reviewed", expected_version=row.version, revise=True)
            return self.view(db, row)

    def withdraw(self, customer, request_id, version):
        with self.store.constraint_guard() as db:
            row, order = self._request(db, request_id, customer)
            if row.review_state == "Withdrawn":
                return self.view(db, row)
            if row.version != version or row.review_state not in ACTIVE:
                raise IntakeError("CONFLICT", "This request changed or was already applied. Refresh your orders.")
            if row.quote_id:
                self.approvals.invalidate(db, RecordRef("Quote", row.quote_id, row.quote_version), ApprovalKind.QUOTATION,
                                          "Customer withdrew amendment.", actor_id=customer.user_id)
            row.review_state = "Withdrawn"
            self.store.commit(db, row, actor_id=customer.user_id, action="order_change.withdrawn", expected_version=version, revise=True)
            return self.view(db, row)

    def _price(self, db, order, terms):
        # A detached proposed order is never added to the session or saved.
        proposed = Order(order_id=order.order_id, customer_id=order.customer_id, required_at=order.required_at,
                        allergen_concerns=order.allergen_concerns, collection_kind=order.collection_kind,
                        items=[OrderItem(product_id=i.product_id, quantity=i.quantity, flavour=i.flavour, theme=i.theme,
                                         customisation=i.customisation, resolved_recipe_id=i.resolved_recipe_id) for i in order.items])
        self._apply_terms(proposed, terms)
        inventory = InventoryService(self.store)
        needs = inventory.calculate_needs(db, proposed)
        products = {p.product_id: p for p in db.scalars(select(Product))}
        costs = {i.item_id: i.unit_cost for i in db.scalars(select(InventoryItem))}
        settings = db.scalars(select(BakerySettings)).one()
        accepted = db.get(Quote, (order.accepted_quote_id, order.accepted_quote_version)) if order.accepted_quote_id else None
        inputs = CostingInputs(
            lines=tuple(QuoteLine(str(i.product_id), i.quantity, products[i.product_id].labour_minutes,
                                  products[i.product_id].packaging_cost) for i in proposed.items),
            ingredient_requirements=tuple(IngredientRequirement(str(k), v, costs[k]) for k, v in needs.items()),
            labour_rate_per_hour=settings.labour_rate_per_hour, target_margin=settings.target_profit_margin,
            additional_costs=(AdditionalCost("Existing additional costs", accepted.additional_cost),) if accepted and accepted.additional_cost else (),
        )
        return FinanceService().calculate_quote(inputs), needs

    def _prepare_quote(self, db, row, order, actor):
        calc, _ = self._price(db, order, row.proposed_terms)
        latest = db.scalar(select(Quote).where(Quote.order_id == order.order_id).order_by(Quote.version.desc()).limit(1))
        quote = Quote(order_id=order.order_id, pending_change_id=row.change_id, source_order_version=order.version + 1,
                      material_cost=calc.material_cost, labour_cost=calc.labour_cost, packaging_cost=calc.packaging_cost,
                      additional_cost=calc.additional_cost, target_margin=calc.target_margin, total=calc.total,
                      requires_manual_review=True, explanation="Revised terms require owner approval and your acceptance.")
        if latest:
            quote.quote_id = latest.quote_id
        ref = self.store.commit(db, quote, actor_id=actor, action="quote.amendment_prepared",
                                expected_version=latest.version if latest else None)
        self.approvals.request(db, ref, ApprovalKind.QUOTATION, actor_id=actor)
        return quote

    def accept(self, customer, request_id, version):
        with self.store.constraint_guard() as db:
            row, order = self._request(db, request_id, customer)
            if row.review_state == "Applied" and row.quote_id:
                return self.view(db, row)
            if row.version != version or row.review_state != "Quoted":
                raise IntakeError("STALE_VERSION", "Review the latest owner-approved amendment before accepting.")
            self._original(db, row, order)
            ref = RecordRef("Quote", row.quote_id, row.quote_version)
            quote = self.store.load(db, Quote, row.quote_id)
            if (quote.version != row.quote_version or quote.pending_change_id != row.change_id
                    or not self.approvals.is_approved(db, ref, ApprovalKind.QUOTATION)):
                raise IntakeError("STALE_VERSION", "This amendment quotation is no longer approved.")
            calc, needs = self._price(db, order, row.proposed_terms)
            fields = ("material_cost", "labour_cost", "packaging_cost", "additional_cost", "target_margin", "total")
            if any(getattr(quote, f) != getattr(calc, f) for f in fields):
                raise IntakeError("STALE_VERSION", "Pricing inputs changed; the owner must prepare a new quotation.")
            quote.customer_decision = CustomerDecision.ACCEPTED
            self.store.commit(db, quote, actor_id=customer.user_id, action="quote.amendment_accepted", expected_version=quote.version)
            self._apply_terms(order, row.proposed_terms)
            order.status = "Confirmed"
            order.accepted_quote_id, order.accepted_quote_version = quote.quote_id, quote.version
            self.store.commit(db, order, actor_id=customer.user_id, action="order.amendment_applied", expected_version=order.version, revise=True)
            shortages = InventoryService(self.store).check_stock(db, needs, exclude_orders={order.order_id})
            self.store.commit(db, ShoppingList(order_id=order.order_id, source_order_version=order.version,
                requirements={str(k): str(v) for k, v in needs.items()}, shortages={str(k): str(v) for k, v in shortages.items()}),
                actor_id=customer.user_id, action="order_change.stock_rechecked")
            self._invalidate_plans(db, order, customer.user_id)
            row.review_state, row.applied_at = "Applied", datetime.now(UTC)
            self.store.commit(db, row, actor_id=customer.user_id, action="order_change.applied", expected_version=row.version, revise=True)
            return self.view(db, row)

    def view(self, db, row, owner=False):
        quote = db.get(Quote, (row.quote_id, row.quote_version)) if row.quote_id else None
        show_quote = quote is not None and (owner or row.review_state in ("Quoted", "Applied"))
        return {"id": str(row.change_id), "orderId": str(row.order_id), "version": row.version,
                "kind": row.kind, "status": row.review_state, "message": row.message,
                "proposed": row.proposed_terms, "ownerNote": row.owner_note,
                "quoteTotal": str(quote.total) if show_quote else None,
                "createdAt": row.created_at.astimezone(UTC).isoformat()}

    def listing(self, customer=None):
        with self.store.unit_of_work() as db:
            query = select(OrderChangeRequest).join(Order, Order.order_id == OrderChangeRequest.order_id)
            if customer:
                query = query.where(Order.customer_id == customer.customer_id)
            return [self.view(db, row, owner=customer is None) for row in db.scalars(query.order_by(OrderChangeRequest.created_at.desc()))]
