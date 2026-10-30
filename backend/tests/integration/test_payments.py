"""BAKE-39: local mock payment for a confirmed order (T-PAYMENT-REPLAY), on PostgreSQL.

Jira tests: a duplicate callback records one transaction; a failed payment
changes nothing; an amount mismatch is rejected.

The confirmed order is set up directly in the store, as BAKE-23's TX-ACCEPT
leaves it: an owner-approved quote version, accepted by the customer, and the
order Confirmed against that version.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.pricing_bookkeeping.payments import (
    GatewayCallback,
    MockPaymentGateway,
    PaymentError,
    PaymentService,
)
from app.shared.approvals import ApprovalKind, ApprovalService
from app.shared.passwords import hash_password
from app.shared.records import (
    ApprovalDecision,
    AuditEntry,
    Customer,
    CustomerDecision,
    MockPayment,
    Order,
    OrderItem,
    OrderStatus,
    PaymentStatus,
    Quote,
    Transaction,
    UserAccount,
)

REQUIRED_AT = datetime.now(UTC) + timedelta(days=14)
TOTAL = Decimal("94.23")


@pytest.fixture
def payments(store) -> PaymentService:
    return PaymentService(store)


def confirmed_order(
    store,
    seeded,
    *,
    approved=True,
    decision=CustomerDecision.ACCEPTED,
    status=OrderStatus.CONFIRMED,
    total=TOTAL,
) -> uuid.UUID:
    """An order confirmed from an accepted quote, unless the arguments say otherwise."""
    approvals = ApprovalService(store)
    with store.unit_of_work() as session:
        order = Order(
            customer_id=seeded.customer_id, required_at=REQUIRED_AT, allergen_concerns=[],
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=24,
                             customisation="standard")],
        )
        store.commit(session, order, actor_id=seeded.customer_user_id, action="order.draft")
        quote = Quote(
            order_id=order.order_id, source_order_version=order.version,
            material_cost=Decimal("14.96"), labour_cost=Decimal("45.00"),
            packaging_cost=Decimal("6.00"), additional_cost=Decimal("0.00"),
            target_margin=Decimal("0.3000"), total=total, customer_decision=decision,
        )
        ref = store.commit(session, quote, actor_id=seeded.owner_id, action="quote.draft")
        request_id = approvals.request(session, ref, ApprovalKind.QUOTATION, actor_id=seeded.owner_id)
        if approved:
            approvals.decide(session, request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)
        order.status = status
        if status != OrderStatus.QUOTED:
            order.accepted_quote_id, order.accepted_quote_version = ref.record_id, ref.version
        store.commit(session, order, actor_id=seeded.customer_user_id, action="order.confirmed",
                     expected_version=order.version)
        return order.order_id


def callback(payment, outcome=PaymentStatus.SUCCEEDED, amount=None) -> GatewayCallback:
    return GatewayCallback(payment.payment_id, outcome, payment.amount if amount is None else amount)


def count(store, model, *where) -> int:
    with store.unit_of_work() as session:
        return session.scalar(select(func.count()).select_from(model).where(*where))


def stored_payment(store, payment_id) -> MockPayment:
    with store.unit_of_work() as session:
        return store.load(session, MockPayment, payment_id)


def order_row(store, order_id) -> Order:
    with store.unit_of_work() as session:
        return store.load(session, Order, order_id)


# Starting a payment -----------------------------------------------------------------


def test_payment_amount_is_the_accepted_quote_total_and_start_is_stable(store, seeded, payments):
    order_id = confirmed_order(store, seeded)

    payment = payments.start(order_id, customer_id=seeded.customer_id)
    again = payments.start(order_id, customer_id=seeded.customer_id)

    assert payment.amount == TOTAL
    assert payment.status == PaymentStatus.CREATED
    assert (again.payment_id, again.replayed) == (payment.payment_id, True)
    assert count(store, MockPayment) == 1
    assert count(store, Transaction) == 0


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ({"status": OrderStatus.QUOTED}, "CONFLICT"),
        ({"status": OrderStatus.CANCELLED}, "CONFLICT"),
        ({"approved": False}, "APPROVAL_REQUIRED"),
        ({"decision": CustomerDecision.PENDING}, "APPROVAL_REQUIRED"),
        ({"total": Decimal("0.00")}, "CONFLICT"),
    ],
)
def test_only_an_order_confirmed_from_an_accepted_approved_quote_can_be_paid(
    store, seeded, payments, setup, code
):
    order_id = confirmed_order(store, seeded, **setup)
    with pytest.raises(PaymentError) as refused:
        payments.start(order_id, customer_id=seeded.customer_id)
    assert refused.value.code == code
    assert count(store, MockPayment) == 0


def test_a_superseded_quote_version_cannot_be_paid(store, seeded, payments):
    order_id = confirmed_order(store, seeded)
    order = order_row(store, order_id)
    with store.unit_of_work() as session:
        accepted = store.load(session, Quote, order.accepted_quote_id)
        newer = Quote(**{c: getattr(accepted, c) for c in (
            "quote_id", "order_id", "source_order_version", "material_cost", "labour_cost",
            "packaging_cost", "additional_cost", "target_margin", "total")})
        store.commit(session, newer, actor_id=seeded.owner_id, action="quote.revised",
                     expected_version=accepted.version)
    with pytest.raises(PaymentError) as refused:
        payments.start(order_id, customer_id=seeded.customer_id)
    assert refused.value.code == "APPROVAL_REQUIRED"


def test_another_customer_gets_not_found(store, seeded, payments):
    order_id = confirmed_order(store, seeded)
    payment = payments.start(order_id, customer_id=seeded.customer_id)
    for attempt in (
        lambda: payments.start(order_id, customer_id=uuid.uuid4()),
        lambda: payments.payment_for_customer(payment.payment_id, customer_id=uuid.uuid4()),
        lambda: payments.payments(order_id, customer_id=uuid.uuid4()),
    ):
        with pytest.raises(PaymentError) as refused:
            attempt()
        assert refused.value.code == "NOT_FOUND"


# Callbacks (TX-PAYMENT-SUCCESS) ------------------------------------------------------


def test_success_records_one_income_with_the_payment(store, seeded, payments):
    order_id = confirmed_order(store, seeded)
    payment = payments.start(order_id, customer_id=seeded.customer_id)
    version_before = order_row(store, order_id).version

    settled = payments.complete(callback(payment))

    assert settled.status == PaymentStatus.SUCCEEDED and not settled.replayed
    with store.unit_of_work() as session:
        [income] = session.scalars(select(Transaction)).all()
        assert (income.kind, income.amount, income.source_type) == ("Income", TOTAL, "OrderPayment")
        assert income.source_reference == str(payment.payment_id).upper()
        assert (income.order_id, income.created_by) == (order_id, seeded.customer_user_id)
        assert settled.transaction_id == income.transaction_id
    assert count(store, AuditEntry, AuditEntry.action == "payment.settled") == 1
    assert count(store, AuditEntry, AuditEntry.action == "finance.transaction_recorded") == 1
    # Paying does not change the order.
    assert order_row(store, order_id).version == version_before


def test_duplicate_success_callback_records_one_transaction(store, seeded, payments):
    payment = payments.start(confirmed_order(store, seeded), customer_id=seeded.customer_id)
    first = payments.complete(callback(payment))
    repeat = payments.complete(callback(payment))

    assert repeat.replayed and repeat.transaction_id == first.transaction_id
    assert count(store, Transaction) == 1
    assert count(store, AuditEntry, AuditEntry.action == "payment.settled") == 1


def test_concurrent_success_callbacks_record_one_transaction(store, seeded, payments):
    payment = payments.start(confirmed_order(store, seeded), customer_id=seeded.customer_id)
    start = threading.Barrier(4)
    outcomes = []

    def deliver():
        start.wait()
        outcomes.append(payments.complete(callback(payment)))

    threads = [threading.Thread(target=deliver) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(outcomes) == 4
    assert sum(not outcome.replayed for outcome in outcomes) == 1
    assert len({outcome.transaction_id for outcome in outcomes}) == 1
    assert count(store, Transaction) == 1


def test_failed_payment_changes_nothing_and_can_be_retried(store, seeded, payments):
    order_id = confirmed_order(store, seeded)
    before = order_row(store, order_id)
    payment = payments.start(order_id, customer_id=seeded.customer_id)

    failed = payments.complete(callback(payment, PaymentStatus.FAILED))
    late_success = payments.complete(callback(payment))

    assert failed.status == PaymentStatus.FAILED and failed.transaction_id is None
    # A settled payment keeps its outcome; a late success callback adds nothing.
    assert (late_success.status, late_success.replayed) == (PaymentStatus.FAILED, True)
    assert count(store, Transaction) == 0
    after = order_row(store, order_id)
    assert (after.status, after.version) == (before.status, before.version)

    retry = payments.start(order_id, customer_id=seeded.customer_id)
    assert retry.payment_id != payment.payment_id
    assert payments.complete(callback(retry)).status == PaymentStatus.SUCCEEDED
    assert count(store, Transaction) == 1


def test_amount_mismatch_is_rejected_and_nothing_changes(store, seeded, payments):
    payment = payments.start(confirmed_order(store, seeded), customer_id=seeded.customer_id)

    with pytest.raises(PaymentError) as refused:
        payments.complete(callback(payment, amount=Decimal("1.00")))

    assert refused.value.code == "VALIDATION_FAILED"
    assert stored_payment(store, payment.payment_id).status == PaymentStatus.CREATED
    assert count(store, Transaction) == 0


def test_a_paid_order_cannot_be_paid_again(store, seeded, payments):
    order_id = confirmed_order(store, seeded)
    payments.complete(callback(payments.start(order_id, customer_id=seeded.customer_id)))

    with pytest.raises(PaymentError) as refused:
        payments.start(order_id, customer_id=seeded.customer_id)
    assert refused.value.code == "CONFLICT"
    assert count(store, MockPayment) == 1


def test_ledger_failure_leaves_the_payment_unpaid(store, seeded, payments, monkeypatch):
    payment = payments.start(confirmed_order(store, seeded), customer_id=seeded.customer_id)

    def ledger_fails(*args, **kwargs):
        raise RuntimeError("ledger unavailable")

    with monkeypatch.context() as patched:
        patched.setattr(payments.ledger, "record_in", ledger_fails)
        with pytest.raises(RuntimeError):
            payments.complete(callback(payment))

    assert stored_payment(store, payment.payment_id).status == PaymentStatus.CREATED
    assert count(store, Transaction) == 0
    # The provider retries the callback: it now succeeds exactly once.
    assert payments.complete(callback(payment)).status == PaymentStatus.SUCCEEDED
    assert count(store, Transaction) == 1


def test_a_cancelled_order_is_not_charged_by_an_open_payment(store, seeded, payments):
    order_id = confirmed_order(store, seeded)
    payment = payments.start(order_id, customer_id=seeded.customer_id)
    with store.unit_of_work() as session:
        order = store.load(session, Order, order_id)
        order.status = OrderStatus.CANCELLED
        store.commit(session, order, actor_id=seeded.owner_id, action="order.cancelled",
                     expected_version=order.version)

    settled = payments.complete(callback(payment))

    # Settled as Failed (review of #34): closed, with the reason, and no income.
    assert (settled.status, settled.transaction_id) == (PaymentStatus.FAILED, None)
    assert "Cancelled" in settled.reason
    assert stored_payment(store, payment.payment_id).status == PaymentStatus.FAILED
    assert count(store, Transaction) == 0


def test_a_withdrawn_approval_after_start_records_no_income(store, seeded, payments):
    order_id = confirmed_order(store, seeded)
    payment = payments.start(order_id, customer_id=seeded.customer_id)
    with store.unit_of_work() as session:
        ApprovalService(store).invalidate(
            session, payment.quote, ApprovalKind.QUOTATION, "test", actor_id=seeded.owner_id
        )

    settled = payments.complete(callback(payment))

    assert (settled.status, settled.transaction_id) == (PaymentStatus.FAILED, None)
    assert "approved" in settled.reason
    assert count(store, Transaction) == 0
    with pytest.raises(PaymentError) as refused:
        payments.start(order_id, customer_id=seeded.customer_id)
    assert refused.value.code == "APPROVAL_REQUIRED"


def test_a_newer_quote_version_after_start_records_no_income(store, seeded, payments):
    order_id = confirmed_order(store, seeded)
    payment = payments.start(order_id, customer_id=seeded.customer_id)
    with store.unit_of_work() as session:
        accepted = store.load(session, Quote, payment.quote.record_id)
        newer = Quote(**{c: getattr(accepted, c) for c in (
            "quote_id", "order_id", "source_order_version", "material_cost", "labour_cost",
            "packaging_cost", "additional_cost", "target_margin", "total")})
        store.commit(session, newer, actor_id=seeded.owner_id, action="quote.revised",
                     expected_version=accepted.version)

    settled = payments.complete(callback(payment))

    assert (settled.status, settled.transaction_id) == (PaymentStatus.FAILED, None)
    assert count(store, Transaction) == 0


# HTTP ---------------------------------------------------------------------------------


@pytest.fixture
def app(database_url, seeded):
    return create_app(Settings(_env_file=None, database_url=database_url,
                               environment="test", app_secret="test-secret-" * 4))


def login(client, role="customer"):
    response = client.post("/api/auth/login", json={"email": f"{role}@bakeflow.test",
                           "password": f"{role}-test-password"}, headers={"X-Requested-With": "BakeFlow"})
    assert response.status_code == 204, response.text
    return {"X-CSRF-Token": client.get("/api/session").json()["csrfToken"]}


def test_api_pays_once_with_the_quote_amount(app, store, seeded):
    order_id = confirmed_order(store, seeded)
    with TestClient(app) as client:
        headers = login(client)
        started = client.post(f"/api/orders/{order_id}/payments", headers=headers)
        assert started.status_code == 201, started.text
        assert (started.json()["amount"], started.json()["status"]) == ("94.23", "Created")
        payment_id = started.json()["id"]
        assert client.post(f"/api/orders/{order_id}/payments", headers=headers).status_code == 200

        # The client can never supply an amount.
        smuggled = client.post(f"/api/payments/{payment_id}/mock-checkout",
                               json={"outcome": "Succeeded", "amount": "1.00"}, headers=headers)
        assert smuggled.status_code == 422

        paid = client.post(f"/api/payments/{payment_id}/mock-checkout",
                           json={"outcome": "Succeeded"}, headers=headers)
        assert paid.status_code == 200, paid.text
        assert (paid.json()["status"], paid.json()["paid"], paid.json()["replayed"]) == (
            "Succeeded", True, False)
        repeat = client.post(f"/api/payments/{payment_id}/mock-checkout",
                             json={"outcome": "Succeeded"}, headers=headers)
        assert repeat.json()["replayed"] is True

        listed = client.get(f"/api/orders/{order_id}/payments").json()["payments"]
        assert [p["status"] for p in listed] == ["Succeeded"]
        assert client.post(f"/api/orders/{order_id}/payments", headers=headers).status_code == 409
    assert count(store, Transaction) == 1


def test_api_failed_payment_then_retry(app, store, seeded):
    order_id = confirmed_order(store, seeded)
    with TestClient(app) as client:
        headers = login(client)
        first = client.post(f"/api/orders/{order_id}/payments", headers=headers).json()["id"]
        failed = client.post(f"/api/payments/{first}/mock-checkout",
                             json={"outcome": "Failed"}, headers=headers)
        assert (failed.json()["status"], failed.json()["paid"]) == ("Failed", False)
        retry = client.post(f"/api/orders/{order_id}/payments", headers=headers)
        assert retry.status_code == 201 and retry.json()["id"] != first
    assert count(store, Transaction) == 0


def test_api_is_for_the_ordering_customer_only(app, store, seeded):
    order_id = confirmed_order(store, seeded)
    uid = uuid.uuid4()
    with store.unit_of_work() as db:
        for record in [UserAccount(user_id=uid, email="other@bakeflow.test",
                                   password_hash=hash_password("other-test-password"), role="Customer"),
                       Customer(customer_id=uuid.uuid4(), user_id=uid, name="Other", contact="test")]:
            store.commit(db, record, actor_id=seeded.owner_id, action="test.seed")
    with TestClient(app) as client:
        assert client.post(f"/api/orders/{order_id}/payments").status_code == 401
        headers = login(client)
        assert client.post(f"/api/orders/{order_id}/payments").status_code == 403  # no CSRF token
        payment_id = client.post(f"/api/orders/{order_id}/payments", headers=headers).json()["id"]

        headers = login(client, "other")
        assert client.post(f"/api/orders/{order_id}/payments", headers=headers).status_code == 404
        assert client.post(f"/api/payments/{payment_id}/mock-checkout",
                           json={"outcome": "Succeeded"}, headers=headers).status_code == 404

        headers = login(client, "owner")
        assert client.post(f"/api/orders/{order_id}/payments", headers=headers).status_code == 403
    assert count(store, Transaction) == 0


def test_mock_gateway_reports_the_stored_amount(store, seeded, payments):
    payment = payments.start(confirmed_order(store, seeded), customer_id=seeded.customer_id)
    reported = MockPaymentGateway().checkout(payment, PaymentStatus.SUCCEEDED)
    assert (reported.payment_id, reported.amount) == (payment.payment_id, TOTAL)
    with pytest.raises(PaymentError):
        MockPaymentGateway().checkout(payment, "Refunded")
