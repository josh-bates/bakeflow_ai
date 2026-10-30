"""UC-02 consent, stale requests, review and atomic commercial amendments."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.sales.changes import ChangeService
from app.sales.change_agent import mock_change
from app.sales.intake import CustomerIdentity, IntakeError
from app.shared.records import Order, OrderItem, OrderChangeRequest, Quote, InventoryItem, Transaction
from app.shared.approvals import ApprovalService, ApprovalKind
from app.shared.store import RecordValidationError
from app.inventory_production import InventoryService
from app.pricing_bookkeeping.quotes import QuoteService
from app.workflow.order_flow import AIOperationFailed
from app.llm.gateway import MockLLMClient

SECRET = b"test-order-change-secret-1234567890"


def customer(seed):
    return CustomerIdentity(seed.customer_user_id, seed.customer_id, "Customer")


def draft(store, seeded):
    with store.unit_of_work() as db:
        order = Order(customer_id=seeded.customer_id, required_at=datetime.now(UTC)+timedelta(days=14), allergen_concerns=[],
                      items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=24, customisation="standard")])
        return store.commit(db, order, actor_id=seeded.customer_user_id, action="order.draft_created").record_id


def quoted(store, seeded, order_id, accept=False):
    InventoryService(store).run_stock_check(order_id, expected_order_version=1, actor_id=seeded.owner_id)
    service = QuoteService(store)
    q = service.draft(order_id, owner_id=seeded.owner_id)
    service.approve(q.ref, owner_id=seeded.owner_id)
    if accept:
        service.accept(q.ref, customer_id=seeded.customer_id)
    return q


def preview(store, seeded, order_id, kind="Change", message="change quantity to 36"):
    service = ChangeService(store, SECRET, mock_change(message))
    return service, service.preview(customer(seeded), order_id, kind, message)


def count(store, model):
    with store.unit_of_work() as db:
        return db.scalar(select(func.count()).select_from(model))


def state(store, order_id):
    with store.unit_of_work() as db:
        o = store.load(db, Order, order_id)
        return o.status, o.version, o.items[0].quantity, o.accepted_quote_version


def test_unconfirmed_and_withdrawn_preview_never_save(store, seeded):
    oid = draft(store, seeded)
    before = state(store, oid)
    _, result = preview(store, seeded, oid)
    assert result["confirmationToken"] and result["proposed"]["quantity"] == 36
    assert count(store, OrderChangeRequest) == 0
    assert state(store, oid) == before  # discarding the preview performs no write


def test_confirmed_change_invalidates_old_quote_and_retry_is_idempotent(store, seeded):
    oid = draft(store, seeded)
    old = quoted(store, seeded, oid)
    service, result = preview(store, seeded, oid)
    token = result["confirmationToken"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: service.confirm(customer(seeded), token), range(2)))
    assert answers[0] == answers[1]
    assert state(store, oid) == ("Quoted", 2, 36, None)
    with store.unit_of_work() as db:
        assert not ApprovalService(store).is_approved(db, old.ref, ApprovalKind.QUOTATION)
    assert QuoteService(store).customer_view(oid, customer_id=seeded.customer_id) is None
    assert count(store, OrderChangeRequest) == 1


def test_stage_moves_after_preview_are_rejected_even_without_version_change(store, seeded):
    oid = draft(store, seeded)
    service, result = preview(store, seeded, oid, "Cancel", "Please cancel")
    quoted(store, seeded, oid)
    with pytest.raises(IntakeError, match="changed"):
        service.confirm(customer(seeded), result["confirmationToken"])
    assert state(store, oid) == ("Quoted", 1, 24, None)
    assert count(store, OrderChangeRequest) == 0


def test_save_failure_rolls_back_order_and_approval_then_can_retry(store, seeded, monkeypatch):
    oid = draft(store, seeded)
    old = quoted(store, seeded, oid)
    service, result = preview(store, seeded, oid)
    original = store.commit
    def fail(db, row, **kwargs):
        if isinstance(row, OrderChangeRequest):
            raise RecordValidationError("simulated save failure")
        return original(db, row, **kwargs)
    monkeypatch.setattr(store, "commit", fail)
    with pytest.raises(RecordValidationError):
        service.confirm(customer(seeded), result["confirmationToken"])
    assert state(store, oid) == ("Quoted", 1, 24, None)
    assert count(store, OrderChangeRequest) == 0
    with store.unit_of_work() as db:
        assert ApprovalService(store).is_approved(db, old.ref, ApprovalKind.QUOTATION)
    monkeypatch.setattr(store, "commit", original)
    assert service.confirm(customer(seeded), result["confirmationToken"])["status"] == "Applied"


def test_confirmed_terms_change_only_after_owner_quote_approval_and_customer_acceptance(store, seeded):
    oid = draft(store, seeded)
    old = quoted(store, seeded, oid, accept=True)
    service, p = preview(store, seeded, oid)
    assert p["route"] == "Requote"
    request = service.confirm(customer(seeded), p["confirmationToken"])
    assert state(store, oid) == ("Confirmed", 1, 24, old.ref.version)
    rid = uuid.UUID(request["id"])
    request = service.decide(seeded.owner_id, rid, request["version"], "PrepareQuote", "Recheck these terms")
    assert request["status"] == "AwaitingApproval"
    assert service.listing(customer(seeded))[0]["quoteTotal"] is None
    with pytest.raises(IntakeError):
        service.accept(customer(seeded), rid, request["version"])
    request = service.decide(seeded.owner_id, rid, request["version"], "ApproveQuote", "Approved revised price")
    assert request["status"] == "Quoted" and request["quoteTotal"]
    assert state(store, oid) == ("Confirmed", 1, 24, old.ref.version)
    applied = service.accept(customer(seeded), rid, request["version"])
    assert applied["status"] == "Applied"
    assert state(store, oid) == ("Confirmed", 2, 36, 2)
    assert service.accept(customer(seeded), rid, request["version"]) == applied
    with store.unit_of_work() as db:
        assert db.get(Quote, (old.ref.record_id, 1)).customer_decision == "Accepted"
        assert db.get(Quote, (old.ref.record_id, 2)).source_order_version == 2


def test_pending_request_can_be_withdrawn_without_applying_terms(store, seeded):
    oid = draft(store, seeded)
    quoted(store, seeded, oid, accept=True)
    service, p = preview(store, seeded, oid)
    request = service.confirm(customer(seeded), p["confirmationToken"])
    before = state(store, oid)
    result = service.withdraw(customer(seeded), uuid.UUID(request["id"]), request["version"])
    assert result["status"] == "Withdrawn" and state(store, oid) == before
    with pytest.raises(IntakeError, match="withdrawn"):
        service.confirm(customer(seeded), p["confirmationToken"])


def test_cancellation_does_not_change_inventory_or_ledger(store, seeded):
    oid = draft(store, seeded)
    with store.unit_of_work() as db:
        stock = {i.item_id: i.quantity_on_hand for i in db.scalars(select(InventoryItem))}
    service, p = preview(store, seeded, oid, "Cancel", "Cancel please")
    service.confirm(customer(seeded), p["confirmationToken"])
    assert state(store, oid)[0] == "Cancelled"
    assert count(store, Transaction) == 0
    with store.unit_of_work() as db:
        assert {i.item_id: i.quantity_on_hand for i in db.scalars(select(InventoryItem))} == stock
    with pytest.raises(IntakeError):
        preview(store, seeded, oid, "Cancel", "Cancel again")


@pytest.mark.parametrize("kind,status,message", [("Refund", "Completed", "Please refund"), ("Change", "InProduction", "change quantity to 36"), ("Cancel", "InProduction", "Cancel")])
def test_exceptions_are_review_only(store, seeded, kind, status, message):
    oid = draft(store, seeded)
    with store.unit_of_work() as db:
        o = store.load(db, Order, oid)
        o.status = status
        store.commit(db, o, actor_id=seeded.owner_id, action="test.stage", expected_version=1)
    service, p = preview(store, seeded, oid, kind, message)
    assert p["route"] == "OwnerReview"
    result = service.confirm(customer(seeded), p["confirmationToken"])
    assert result["status"] == "Pending" and state(store, oid)[:3] == (status, 1, 24)
    assert service.listing()[0]["id"] == result["id"]


def test_unreliable_ai_stops_after_three_attempts(store, seeded):
    oid = draft(store, seeded)
    client = MockLLMClient(default_reply='{"scope":"Quantity","quantity":999,"evidence":"999"}')
    service = ChangeService(store, SECRET, client)
    with pytest.raises(AIOperationFailed):
        service.preview(customer(seeded), oid, "Change", "change quantity to 36")
    assert len(client.calls) == 3 and count(store, OrderChangeRequest) == 0
    assert state(store, oid)[2] == 24


def test_http_auth_csrf_and_save_failure_returns_no_success(store, seeded, database_url, monkeypatch):
    oid = draft(store, seeded)
    app = create_app(Settings(_env_file=None, database_url=database_url, environment="test", app_secret=SECRET.decode()))
    with TestClient(app) as c:
        assert c.get("/api/order-changes").status_code == 401
        c.post("/api/auth/login", json={"email":"customer@bakeflow.test","password":"customer-test-password"}, headers={"X-Requested-With":"BakeFlow"})
        headers = {"X-CSRF-Token": c.get("/api/session").json()["csrfToken"]}
        path = f"/api/orders/{oid}/changes/preview"
        body = {"kind":"Cancel","message":"Cancel please"}
        assert c.post(path, json=body).status_code == 403
        assert c.get("/api/owner/order-changes").status_code == 403
        p = c.post(path, json=body, headers=headers).json()
        original = type(store).commit
        def fail(self, db, row, **kwargs):
            if isinstance(row, OrderChangeRequest):
                raise RecordValidationError("simulated failure")
            return original(self, db, row, **kwargs)
        monkeypatch.setattr(type(store), "commit", fail)
        result = c.post("/api/order-changes/confirm", json={"confirmationToken":p["confirmationToken"]}, headers=headers)
        assert result.status_code == 503 and "not confirmed" in result.json()["detail"]
        assert state(store, oid)[0] == "Draft" and count(store, OrderChangeRequest) == 0


def test_pending_amendment_pauses_effective_work_and_preserves_commitments(store, seeded):
    from tests.integration.test_schedule_release import plan, approve, released_tasks, order as planned_order
    from app.inventory_production import TaskProgress, ProgressRefused
    from app.shared.records import TaskStatus
    ref = planned_order(store, seeded)
    schedule = plan(store, seeded)
    approve(store, seeded, schedule)
    with store.unit_of_work() as db:
        before = InventoryService(store).committed_needs(db)
    service, p = preview(store, seeded, ref.record_id, "Change", "change quantity to 36")
    row = service.confirm(customer(seeded), p["confirmationToken"])
    assert row["status"] == "Pending"
    with store.unit_of_work() as db:
        assert InventoryService(store).committed_needs(db) == before
    task = released_tasks(store, schedule)[0]
    with pytest.raises(ProgressRefused, match="pending"):
        TaskProgress(store).record(schedule.record_id, task, TaskStatus.IN_PROGRESS, actor_id=seeded.owner_id)
    service.withdraw(customer(seeded), uuid.UUID(row["id"]), row["version"])
    assert state(store, ref.record_id)[:3] == ("Scheduled", 1, 24)


def test_cancel_vs_release_cannot_leave_a_cancelled_order_scheduled(store, seeded):
    from tests.integration.test_schedule_release import plan, approve, order as planned_order
    from app.inventory_production import ReleaseStatus
    ref = planned_order(store, seeded)
    schedule = plan(store, seeded)
    service, p = preview(store, seeded, ref.record_id, "Cancel", "Cancel please")
    def cancel():
        try:
            return service.confirm(customer(seeded), p["confirmationToken"])["status"]
        except IntakeError:
            return "Stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(cancel)
        b = pool.submit(approve, store, seeded, schedule)
        cancelled, released = a.result(), b.result()
    assert (cancelled, released.status, state(store, ref.record_id)[0]) in [
        ("Applied", ReleaseStatus.BLOCKED, "Cancelled"), ("Stale", ReleaseStatus.RELEASED, "Scheduled")]


def test_other_customer_cannot_preview_confirm_read_or_withdraw(store, seeded):
    oid = draft(store, seeded)
    service, p = preview(store, seeded, oid)
    other = CustomerIdentity(uuid.uuid4(), uuid.uuid4(), "Other")
    with pytest.raises(IntakeError) as missing:
        service.preview(other, oid, "Cancel", "Cancel")
    assert missing.value.code == "NOT_FOUND"
    with pytest.raises(IntakeError):
        service.confirm(other, p["confirmationToken"])
    assert service.listing(other) == []
    row = service.confirm(customer(seeded), p["confirmationToken"])
    with pytest.raises(IntakeError) as denied:
        service.withdraw(other, uuid.UUID(row["id"]), row["version"])
    assert denied.value.code == "NOT_FOUND"


def test_accepted_amendment_save_failure_keeps_old_terms_and_quote_decision(store, seeded, monkeypatch):
    oid = draft(store, seeded)
    old = quoted(store, seeded, oid, accept=True)
    service, p = preview(store, seeded, oid)
    row = service.confirm(customer(seeded), p["confirmationToken"])
    rid = uuid.UUID(row["id"])
    row = service.decide(seeded.owner_id, rid, row["version"], "PrepareQuote", "New quote")
    row = service.decide(seeded.owner_id, rid, row["version"], "ApproveQuote", "Approved")
    commit = store.commit
    def fail(db, record, **kwargs):
        if isinstance(record, OrderChangeRequest):
            raise RecordValidationError("disk error")
        return commit(db, record, **kwargs)
    monkeypatch.setattr(store, "commit", fail)
    with pytest.raises(RecordValidationError):
        service.accept(customer(seeded), rid, row["version"])
    assert state(store, oid) == ("Confirmed", 1, 24, 1)
    with store.unit_of_work() as db:
        assert db.get(Quote, (old.ref.record_id, 2)).customer_decision == "Pending"


def test_unpaid_cancel_after_quote_acceptance_does_not_refund_or_change_terms(store, seeded):
    oid = draft(store, seeded)
    quoted(store, seeded, oid, accept=True)
    service, p = preview(store, seeded, oid, "Cancel", "Cancel")
    assert p["route"] == "Apply"
    service.confirm(customer(seeded), p["confirmationToken"])
    assert state(store, oid)[0] == "Cancelled" and count(store, Transaction) == 0


def test_withdrawn_requote_preserves_original_contract_and_payment(store, seeded):
    from app.pricing_bookkeeping.payments import PaymentService
    oid = draft(store, seeded)
    original = quoted(store, seeded, oid, accept=True)
    service, p = preview(store, seeded, oid)
    row = service.confirm(customer(seeded), p["confirmationToken"])
    rid = uuid.UUID(row["id"])
    row = service.decide(seeded.owner_id, rid, row["version"], "PrepareQuote", "Updated quote")
    service.withdraw(customer(seeded), rid, row["version"])
    assert state(store, oid) == ("Confirmed", 1, 24, 1)
    payment = PaymentService(store).start(oid, customer_id=seeded.customer_id)
    assert payment.amount == original.total


def test_collection_change_preserves_explicit_at_or_before(store, seeded):
    oid = draft(store, seeded)
    when = (datetime.now(UTC)+timedelta(days=20)).isoformat()
    service, p = preview(store, seeded, oid, "Change", f"collection before {when}")
    service.confirm(customer(seeded), p["confirmationToken"])
    with store.unit_of_work() as db:
        order = db.get(Order, oid)
        assert order.required_at == datetime.fromisoformat(when) and order.collection_kind == "before"


def test_paid_cancellation_is_review_only(store, seeded):
    from app.pricing_bookkeeping.payments import PaymentService
    oid = draft(store, seeded)
    quoted(store, seeded, oid, accept=True)
    PaymentService(store).start(oid, customer_id=seeded.customer_id)
    service, p = preview(store, seeded, oid, "Cancel", "Cancel please")
    assert p["route"] == "OwnerReview"
    result = service.confirm(customer(seeded), p["confirmationToken"])
    assert result["status"] == "Pending" and state(store, oid)[0] == "Confirmed"
