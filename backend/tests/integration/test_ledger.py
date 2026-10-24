"""BAKE-24: the ledger records each transaction once (UC-06 steps 1-3), on PostgreSQL.

Jira tests: a duplicate submission leaves the ledger unchanged; an invalid
amount is rejected. Architecture T-UC06-REPLAY: a request-key retry is not
posted twice, a repeated source reference is rejected, and an independent
transaction with the same amount is accepted.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.pricing_bookkeeping.ledger import (
    RECORD_ACTION,
    DuplicateTransaction,
    LedgerService,
    RequestKeyConflict,
    TransactionInput,
    TransactionRejected,
)
from app.pricing_bookkeeping.routes import OwnerIdentity, current_owner, make_router
from app.shared.records import AuditEntry, Transaction

OCCURRED = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


@pytest.fixture
def ledger(store) -> LedgerService:
    return LedgerService(store)


def invoice(reference="INV-1001", amount="42.50", **kwargs) -> TransactionInput:
    return TransactionInput(
        kind=kwargs.pop("kind", "Expense"),
        amount=Decimal(amount),
        source_type=kwargs.pop("source_type", "Invoice"),
        source_reference=reference,
        occurred_at=kwargs.pop("occurred_at", OCCURRED),
        **kwargs,
    )


def stored(store) -> list[Transaction]:
    with store.unit_of_work() as session:
        return list(session.scalars(select(Transaction).order_by(Transaction.source_reference)))


def audits(store) -> int:
    with store.unit_of_work() as session:
        return session.scalar(
            select(func.count()).select_from(AuditEntry).where(AuditEntry.action == RECORD_ACTION)
        )


def test_valid_transaction_is_stored_once_with_its_audit(ledger, store, seeded):
    entry = ledger.record(seeded.owner_id, invoice())

    [row] = stored(store)
    assert row.transaction_id == entry.transaction_id
    assert (row.kind, row.amount, row.source_reference) == ("Expense", Decimal("42.50"), "INV-1001")
    assert row.created_by == seeded.owner_id
    assert not entry.replayed
    assert audits(store) == 1


def test_duplicate_source_is_rejected_and_the_ledger_is_unchanged(ledger, store, seeded):
    original = ledger.record(seeded.owner_id, invoice())

    with pytest.raises(DuplicateTransaction) as error:
        ledger.record(seeded.owner_id, invoice(amount="99.00"))

    assert error.value.existing.transaction_id == original.transaction_id
    [row] = stored(store)
    assert row.amount == Decimal("42.50")
    assert audits(store) == 1


def test_duplicate_is_detected_despite_spaces_and_case(ledger, store, seeded):
    ledger.record(seeded.owner_id, invoice("INV-1001"))
    with pytest.raises(DuplicateTransaction):
        ledger.record(seeded.owner_id, invoice("  INV-1001 "))
    with pytest.raises(DuplicateTransaction):
        ledger.record(seeded.owner_id, invoice("inv-1001"))
    assert [row.source_reference for row in stored(store)] == ["INV-1001"]


def test_invalid_amount_is_rejected_and_nothing_is_stored(ledger, store, seeded):
    with pytest.raises(TransactionRejected) as error:
        ledger.record(seeded.owner_id, invoice(amount="-5.00"))

    assert [issue.field for issue in error.value.issues] == ["amount"]
    assert stored(store) == []
    assert audits(store) == 0


def test_same_amount_with_a_different_reference_is_a_separate_transaction(ledger, store, seeded):
    ledger.record(seeded.owner_id, invoice("INV-1001"))
    ledger.record(seeded.owner_id, invoice("INV-1002"))
    assert [row.source_reference for row in stored(store)] == ["INV-1001", "INV-1002"]


def test_retry_with_the_same_request_key_returns_the_original(ledger, store, seeded):
    key = uuid.uuid4()
    first = ledger.record(seeded.owner_id, invoice(request_key=key))
    retry = ledger.record(seeded.owner_id, invoice(request_key=key))

    assert retry.replayed
    assert retry.transaction_id == first.transaction_id
    assert len(stored(store)) == 1
    assert audits(store) == 1


def test_request_key_reused_with_different_details_stores_nothing(ledger, store, seeded):
    key = uuid.uuid4()
    ledger.record(seeded.owner_id, invoice("INV-1001", request_key=key))

    with pytest.raises(RequestKeyConflict):
        ledger.record(seeded.owner_id, invoice("INV-1002", amount="10.00", request_key=key))

    assert [row.source_reference for row in stored(store)] == ["INV-1001"]


def test_links_to_an_order_or_product_must_exist(ledger, store, seeded):
    with pytest.raises(TransactionRejected) as error:
        ledger.record(seeded.owner_id, invoice(order_id=uuid.uuid4(), product_id=uuid.uuid4()))
    assert {issue.field for issue in error.value.issues} == {"order_id", "product_id"}
    assert stored(store) == []


def test_income_for_a_product_is_recorded(ledger, store, seeded):
    cupcakes = seeded.products["Vanilla cupcake"]
    ledger.record(seeded.owner_id, invoice("RCPT-7", amount="94.23", kind="Income",
                                            source_type="Receipt", product_id=cupcakes))
    [row] = stored(store)
    assert (row.kind, row.product_id) == ("Income", cupcakes)


def test_concurrent_submissions_of_one_invoice_store_it_once(ledger, store, seeded):
    start = threading.Barrier(2)
    outcomes = []

    def submit():
        start.wait()
        try:
            outcomes.append(ledger.record(seeded.owner_id, invoice()))
        except DuplicateTransaction as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(stored(store)) == 1
    assert sum(isinstance(outcome, DuplicateTransaction) for outcome in outcomes) == 1


def test_transactions_are_listed_newest_first(ledger, seeded):
    ledger.record(seeded.owner_id, invoice("INV-1", occurred_at=OCCURRED - timedelta(days=2)))
    ledger.record(seeded.owner_id, invoice("INV-2", occurred_at=OCCURRED))
    assert [entry.source_reference for entry in ledger.transactions()] == ["INV-2", "INV-1"]


@pytest.fixture
def api(store, seeded) -> TestClient:
    app = FastAPI()
    app.include_router(make_router(Settings(_env_file=None), store=store))
    app.dependency_overrides[current_owner] = lambda: OwnerIdentity(seeded.owner_id)
    return TestClient(app)


def body(**changes) -> dict:
    return {"kind": "Expense", "amount": "42.50", "sourceType": "Invoice",
            "sourceReference": "INV-1001", "occurredAt": "2026-10-22T20:00:00+11:00"} | changes


def test_api_records_replays_and_rejects_duplicates(api, store):
    key = str(uuid.uuid4())

    created = api.post("/api/finance/transactions", json=body(requestKey=key))
    assert created.status_code == 201
    assert created.json()["amount"] == "42.50"

    replayed = api.post("/api/finance/transactions", json=body(requestKey=key))
    assert replayed.status_code == 200
    assert replayed.json()["id"] == created.json()["id"]

    duplicate = api.post("/api/finance/transactions", json=body())
    assert duplicate.status_code == 409
    assert duplicate.json()["existing"]["id"] == created.json()["id"]

    conflict = api.post("/api/finance/transactions", json=body(requestKey=key, amount="1.00"))
    assert conflict.status_code == 409

    listed = api.get("/api/finance/transactions").json()["transactions"]
    assert [entry["id"] for entry in listed] == [created.json()["id"]]
    assert len(stored(store)) == 1


def test_api_lists_every_field_to_fix(api, store):
    response = api.post("/api/finance/transactions",
                        json=body(amount="12.345", sourceType="Cash", sourceReference=" "))
    assert response.status_code == 422
    assert {issue["field"] for issue in response.json()["issues"]} == {
        "amount", "source_type", "source_reference"}
    assert stored(store) == []
