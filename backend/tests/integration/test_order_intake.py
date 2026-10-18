"""UC-01 PostgreSQL transaction and concurrent confirmation checks."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC, timedelta

import pytest
from sqlalchemy import select, func

from app.llm.gateway import MockLLMClient
from app.sales.intake import CustomerIdentity, OrderIntake
from app.shared.records import Order, AuditEntry, InventoryItem


@pytest.fixture
def intake(store, seeded):
    data = {"items": [{"product": "Vanilla cupcake", "quantity": 24, "flavour": "vanilla", "theme": "birthday"}],
            "required_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "collection_kind": "before", "allergen_concerns": [], "budget": None}
    flow = OrderIntake(store, MockLLMClient(default_reply=json.dumps(data)), b"integration-test-key")
    identity = CustomerIdentity(seeded.customer_user_id, seeded.customer_id, "Customer")
    return flow, identity


def test_confirm_commits_draft_and_audit_without_stock_change(intake):
    flow, customer = intake
    with flow.store.unit_of_work() as db:
        before = {i.item_id: i.quantity_on_hand for i in db.scalars(select(InventoryItem))}
    preview = flow.preview(customer, "request")
    receipt = flow.confirm(customer, preview["confirmationToken"])
    assert receipt["status"] == "Draft"
    with flow.store.unit_of_work() as db:
        order = db.scalars(select(Order)).one()
        assert order.collection_kind == "before"
        assert order.allergen_concerns == []
        assert order.items[0].quantity == 24
        audit = db.scalars(select(AuditEntry).where(AuditEntry.action == "order.draft_created")).one()
        assert audit.subject_id == order.order_id
        assert {i.item_id: i.quantity_on_hand for i in db.scalars(select(InventoryItem))} == before


def test_concurrent_confirmations_create_one_order(intake):
    flow, customer = intake
    token = flow.preview(customer, "request")["confirmationToken"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _: flow.confirm(customer, token), range(2)))
    assert receipts[0] == receipts[1]
    with flow.store.unit_of_work() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "order.draft_created")) == 1


def test_transaction_failure_does_not_leave_order_or_audit(intake, monkeypatch):
    flow, customer = intake
    token = flow.preview(customer, "request")["confirmationToken"]
    original = flow.store.commit
    def fail_after_flush(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated transaction failure")
    monkeypatch.setattr(flow.store, "commit", fail_after_flush)
    with pytest.raises(RuntimeError):
        flow.confirm(customer, token)
    with flow.store.unit_of_work() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "order.draft_created")) == 0
