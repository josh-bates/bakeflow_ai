"""BAKE-38: real transactions, replay, crash recovery and customer isolation."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.inventory_production import InventoryService
from app.main import create_app
from app.pricing_bookkeeping.quotes import QuoteService
from app.sales.intake import CustomerIdentity
from app.sales.notifications import deliver_notification, list_notifications
from app.shared.passwords import hash_password
from app.shared.records import AuditEntry, Customer, Notification, Order, OrderItem, UserAccount, WorkflowTransition
from app.workflow.order_flow import WorkflowService, OrderStage
from app.shared.refs import RecordRef


def customer(seeded):
    return CustomerIdentity(seeded.customer_user_id, seeded.customer_id, "Customer")


def draft(store, seeded, customer_id=None):
    with store.unit_of_work() as db:
        order = Order(customer_id=customer_id or seeded.customer_id,
                      required_at=datetime.now(UTC) + timedelta(days=14), allergen_concerns=[],
                      items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=24, customisation="standard")])
        return store.commit(db, order, actor_id=seeded.customer_user_id, action="order.draft_created")


def count(store, model):
    with store.unit_of_work() as db:
        return db.scalar(select(func.count()).select_from(model))


def move(store, seeded, ref, status):
    with store.unit_of_work() as db:
        order = store.load(db, Order, ref.record_id)
        order.status = WorkflowService().move_order(ref, OrderStage(order.status), OrderStage(status),
                                                   actor=str(seeded.owner_id), event_type="test.move").value
        store.commit(db, order, actor_id=seeded.owner_id, action="order.stage", expected_version=order.version)


def test_every_saved_transition_once_and_same_stage_revisit_is_a_new_event(store, seeded):
    ref = draft(store, seeded)
    stages = ["Quoted", "Confirmed", "Scheduled", "Confirmed", "Scheduled", "InProduction", "Completed"]
    for status in stages:
        move(store, seeded, ref, status)
    with store.unit_of_work() as db:
        order = store.load(db, Order, ref.record_id)
        store.commit(db, order, actor_id=seeded.owner_id, action="retry.no_change", expected_version=order.version)
        transitions = db.scalars(select(WorkflowTransition)).all()
        ids = [t.transition_id for t in transitions]
        assert {t.order_version for t in transitions} == {1}
        for t in transitions:
            audit = db.get(AuditEntry, t.event_id)
            assert audit.transition_id == t.transition_id
            assert audit.after_data["status"] == t.to_state
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda key: deliver_notification(store, key), ids * 3))
    assert count(store, Notification) == count(store, WorkflowTransition) == 8
    assert len(list_notifications(store, customer(seeded))) == 8


def test_in_memory_move_and_rollback_never_notify(store, seeded):
    WorkflowService().move_order(RecordRef("Order", uuid.uuid4(), 1), OrderStage.DRAFT, OrderStage.QUOTED,
                                actor=str(seeded.owner_id), event_type="not.saved")
    with pytest.raises(RuntimeError):
        with store.unit_of_work() as db:
            order = Order(customer_id=seeded.customer_id, required_at=datetime.now(UTC))
            store.commit(db, order, actor_id=seeded.owner_id, action="order.draft")
            raise RuntimeError("rollback")
    assert count(store, WorkflowTransition) == count(store, Notification) == 0


def test_failed_delivery_keeps_transition_and_get_uses_same_template_without_writes(store, seeded, monkeypatch):
    import app.sales.notifications as notifications
    original = notifications.deliver_notification
    monkeypatch.setattr(notifications, "deliver_notification", lambda *args: (_ for _ in ()).throw(RuntimeError("delivery down")))
    ref = draft(store, seeded)
    assert count(store, Order) == count(store, WorkflowTransition) == 1
    assert count(store, Notification) == 0
    before = list_notifications(store, customer(seeded))
    assert before[0]["orderId"] == str(ref.record_id)
    assert "not a confirmed sale" in before[0]["message"]
    assert count(store, Notification) == 0  # GET does not repair or invoke AI
    monkeypatch.setattr(notifications, "deliver_notification", original)
    original(store, uuid.UUID(before[0]["id"]))
    assert list_notifications(store, customer(seeded)) == before
    assert count(store, Notification) == 1


def test_templates_work_even_when_ai_is_unavailable(store, seeded, monkeypatch):
    from app.llm.gateway import LLMCallFailed
    def unavailable(*args, **kwargs):
        raise LLMCallFailed("provider unavailable")
    monkeypatch.setattr(WorkflowService, "run_ai_task", unavailable)
    draft(store, seeded)
    move_ref = list_notifications(store, customer(seeded))[0]
    assert move_ref["status"] == "Draft" and "not a confirmed sale" in move_ref["message"]
    assert count(store, Notification) == 1


def test_quote_approval_and_acceptance_notify_only_after_commit_and_not_on_retry(store, seeded):
    ref = draft(store, seeded)
    InventoryService(store).run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)
    quotes = QuoteService(store)
    quote = quotes.draft(ref.record_id, owner_id=seeded.owner_id)
    assert [n["status"] for n in list_notifications(store, customer(seeded))] == ["Draft"]
    quotes.approve(quote.ref, owner_id=seeded.owner_id)
    quotes.approve(quote.ref, owner_id=seeded.owner_id)
    assert [n["status"] for n in list_notifications(store, customer(seeded))] == ["Quoted", "Draft"]
    quotes.accept(quote.ref, customer_id=seeded.customer_id)
    quotes.accept(quote.ref, customer_id=seeded.customer_id)
    assert [n["status"] for n in list_notifications(store, customer(seeded))] == ["Confirmed", "Quoted", "Draft"]
    assert count(store, Notification) == 3


def test_http_notifications_are_private_and_identity_cannot_be_supplied(store, seeded, database_url):
    ref = draft(store, seeded)
    uid, cid = uuid.uuid4(), uuid.uuid4()
    with store.unit_of_work() as db:
        for record in [UserAccount(user_id=uid, email="other@bakeflow.test", password_hash=hash_password("other-test-password"), role="Customer"),
                       Customer(customer_id=cid, user_id=uid, name="Other", contact="test")]:
            store.commit(db, record, actor_id=seeded.owner_id, action="test.seed")
    other_ref = draft(store, seeded, cid)
    app = create_app(Settings(_env_file=None, database_url=database_url, environment="test", app_secret="notification-test-secret-" * 3))
    with TestClient(app) as client:
        assert client.get("/api/notifications").status_code == 401
        for role, expected in [("customer", ref), ("other", other_ref)]:
            assert client.post("/api/auth/login", json={"email": f"{role}@bakeflow.test", "password": f"{role}-test-password"},
                               headers={"X-Requested-With": "BakeFlow"}).status_code == 204
            response = client.get(f"/api/notifications?customer_id={seeded.customer_id}&recipient_id={seeded.customer_user_id}")
            assert response.status_code == 200
            assert [n["orderId"] for n in response.json()["notifications"]] == [str(expected.record_id)]
        client.post("/api/auth/login", json={"email": "owner@bakeflow.test", "password": "owner-test-password"},
                    headers={"X-Requested-With": "BakeFlow"})
        assert client.get("/api/notifications").status_code == 403
