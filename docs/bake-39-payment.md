# BAKE-39: local mock payment for confirmed orders

> **Status: backend done (service, record, migration, API).** The customer's "Pay" button follows once BAKE-23's customer quote page exists, so this PR doesn't stack on #21.

Josh's optional feature (architecture [O3], T-PAYMENT-REPLAY). A customer pays for an order that was confirmed from an accepted, current, owner-approved quote. The payment is simulated locally: there is no bank, no card data, no keys and no real or sandbox provider. A successful payment is recorded once as income in the BAKE-24 ledger.

## What is built

- **Record `MockPayment`** (`mock_payments`, architecture §5.3):
  - `payment_id`, `order_id`, the accepted quote (`accepted_quote_id`, `accepted_quote_version`), `amount NUMERIC(12,2)`, `status` (Created, Succeeded or Failed), `transaction_id`, `created_at`, `processed_at`, and a row version.
  - Database checks: a Succeeded payment always has its income transaction and any other status never has one; a payment has `processed_at` exactly when it has settled; the amount is positive.
  - A partial unique index allows at most **one open or paid payment per order**. A failed payment can be retried with a new one.
  - Migration `bake39_payments`.
- **`PaymentService`** (`backend/app/pricing_bookkeeping/payments.py`):
  - `start(order_id, customer_id)`:
    - **Payable orders:** only an order that is Confirmed (or later, but not Cancelled) whose accepted quote is still the latest version, accepted by the customer and approved by the owner (`ApprovalService.is_approved`, `ApprovalKind.QUOTATION`).
    - **Amount:** copied from that quote's total, two decimal places, once. No route accepts an amount.
    - **Repeats:** starting again returns the open payment (stable `payment_id`). An order that is already paid is refused (`CONFLICT`).
  - `complete(callback)`, TX-PAYMENT-SUCCESS, in one transaction:
    - **Success:** records the income through `LedgerService.record_in` (`OrderPayment`, reference = the payment id, linked to the order), then marks the payment Succeeded with its `transaction_id`. Both commit together or neither does.
    - **Failure:** marks the payment Failed. No income is recorded and the order is unchanged.
    - **Repeated callbacks:** a callback for a payment that has already settled returns the stored outcome (`replayed`) and records nothing.
    - **Amount mismatch:** a callback amount that differs from the stored amount is refused (`VALIDATION_FAILED`) and nothing changes.
    - **Order no longer payable:** a success for an order that has since been cancelled, or whose accepted quote has changed, is refused (`CONFLICT`).
    - **Locking:** the order is locked before the payment, in the same order as `start`, so concurrent callbacks run one after another.
  - `MockPaymentGateway` stands in for a provider's test mode. The customer chooses Succeeded or Failed on the mock checkout, as test card numbers do on a real sandbox. The gateway reports the **stored** amount.
- **`LedgerService.record_in(session, ...)`**: BAKE-24's `record`, inside the caller's unit of work. `record` now calls it, and its behaviour is unchanged.
- **API** (`backend/app/pricing_bookkeeping/payment_routes.py`), customer only (`current_customer`; the middleware checks CSRF on every POST):

  | Route | Result |
  |---|---|
  | `POST /api/orders/{id}/payments` | 201 created, 200 the open payment, 409 already paid or not payable, 404 not this customer's order |
  | `GET /api/orders/{id}/payments` | the order's payments, oldest first |
  | `POST /api/payments/{id}/mock-checkout` `{"outcome": "Succeeded" \| "Failed"}` | the settled payment (`replayed` on a repeat); 422 for any other field, such as an `amount` |

  Amounts are decimal strings, never JSON numbers.

AI agents play no part in amounts or payment state.

## Deviations

- **Live payment processing** was an optional extension excluded in Stage 1 (Fig. 2; §1.3, §4, §15). It is implemented here as a **local simulation** only, to meet the per-member optional-feature requirement (Lab Note 2). This keeps the Stage 1 boundary: no money moves and no external system is contacted.
- **No `WorkflowService.handle(PAYMENT_CALLBACK_*)`.** Architecture §4 routes callbacks through it, but it doesn't exist yet, so the routes call `PaymentService` directly.
- **Recorded actor:** the paying customer's account is recorded as the actor for the payment and its income, since there is no provider account.

## Next

- **Customer UI**, once BAKE-23's customer quote page is merged: a "Pay" button on a confirmed order, the mock checkout (succeed or decline), and the payment status.
- **Migration chain:** `bake39_payments` follows `bake14_sessions`, like #21 (`bake23_quote_explanation`) and #33 (`bake32_receipts`). Whichever of these merges later re-points its `down_revision`, so Alembic keeps one head.

## Verification

`tests/integration/test_payments.py` (PostgreSQL, runs in CI) covers T-PAYMENT-REPLAY and the Jira tests:

- **Starting:**
  - the amount is the accepted quote's total, and starting again returns the same payment;
  - refused: a Quoted, Cancelled, unapproved or not-accepted order, a zero total, and a superseded quote version;
  - another customer gets "not found".
- **Callbacks:**
  - a success records one income (Income, OrderPayment, the payment id, the order, the customer) with its audit entries, and the order is unchanged;
  - **a duplicate callback records one transaction**, and four concurrent callbacks also record exactly one;
  - **a failed payment changes nothing**: no income, order status and version unchanged, a late success callback for it adds nothing, and a retry with a new payment succeeds once;
  - **an amount mismatch is rejected** and the payment stays Created;
  - a paid order cannot be paid again;
  - if the ledger fails, the payment stays unpaid, and the provider's retry then succeeds exactly once;
  - a cancelled order is not charged by its open payment.
- **API (real sign-in and CSRF):**
  - 201 then 200 on start, and an `amount` field is refused with 422;
  - success, a replayed repeat, and 409 for a second payment;
  - a failure followed by a retry;
  - 401 when signed out, 403 without CSRF, 404 for another customer, and 403 for the owner.
