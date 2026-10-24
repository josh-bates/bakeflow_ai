"""BAKE-24: transaction validation (UC-06 2a) and the owner-only route guard."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.pricing_bookkeeping.ledger import TransactionInput, validate_transaction
from app.pricing_bookkeeping.routes import make_router

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def flour_invoice(**changes) -> TransactionInput:
    entry = TransactionInput(
        kind="Expense",
        amount=Decimal("42.50"),
        source_type="Invoice",
        source_reference="INV-1001",
        occurred_at=NOW - timedelta(days=1),
    )
    return replace(entry, **changes)


def fields(entry: TransactionInput) -> set[str]:
    return {issue.field for issue in validate_transaction(entry, now=NOW)}


def test_valid_expense_has_no_issues():
    assert validate_transaction(flour_invoice(), now=NOW) == ()


def test_income_linked_to_an_order_and_product_is_valid():
    entry = flour_invoice(kind="Income", source_type="OrderPayment",
                          order_id=uuid.uuid4(), product_id=uuid.uuid4())
    assert validate_transaction(entry, now=NOW) == ()


def test_time_in_another_zone_is_accepted():
    sydney = timezone(timedelta(hours=11))
    assert validate_transaction(flour_invoice(occurred_at=NOW.astimezone(sydney)), now=NOW) == ()


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"kind": "Refund"}, "kind"),
        ({"amount": Decimal("0")}, "amount"),
        ({"amount": Decimal("-5.00")}, "amount"),
        ({"amount": Decimal("12.345")}, "amount"),
        ({"amount": 12.5}, "amount"),
        ({"amount": "12.50"}, "amount"),
        ({"amount": Decimal("NaN")}, "amount"),
        ({"amount": Decimal("10000000000.00")}, "amount"),
        ({"source_type": "invoice"}, "source_type"),
        ({"source_reference": "   "}, "source_reference"),
        ({"source_reference": "X" * 201}, "source_reference"),
        ({"occurred_at": datetime(2026, 10, 22, 9, 0)}, "occurred_at"),
        ({"occurred_at": NOW + timedelta(hours=1)}, "occurred_at"),
        ({"order_id": "not-a-uuid"}, "order_id"),
        ({"source_type": "OrderPayment"}, "order_id"),
    ],
)
def test_invalid_field_is_reported(changes, field):
    assert fields(flour_invoice(**changes)) == {field}


def test_nul_character_in_a_reference_has_its_own_message():
    [issue] = validate_transaction(flour_invoice(source_reference="INV\x001"), now=NOW)
    assert (issue.field, issue.message) == ("source_reference", "contains a NUL character")


def test_all_invalid_fields_are_reported_together():
    entry = flour_invoice(kind="Gift", amount=Decimal("-1"), source_reference="")
    assert fields(entry) == {"kind", "amount", "source_reference"}


def test_a_few_minutes_of_clock_difference_is_allowed():
    assert validate_transaction(flour_invoice(occurred_at=NOW + timedelta(minutes=2)), now=NOW) == ()


def test_ledger_routes_refuse_requests_without_an_owner():
    app = FastAPI()
    app.include_router(make_router(Settings(_env_file=None)))
    client = TestClient(app)

    assert client.get("/api/finance/transactions").status_code == 401
    response = client.post("/api/finance/transactions", json={
        "kind": "Expense", "amount": "42.50", "sourceType": "Invoice",
        "sourceReference": "INV-1001", "occurredAt": "2026-10-22T09:00:00+11:00",
    })
    assert response.status_code == 401
