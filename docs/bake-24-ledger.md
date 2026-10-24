# BAKE-24: income and expense ledger

Implements UC-06 steps 1-3 (AR-03): the owner records an income or expense, invalid data is rejected with the fields to fix (2a), duplicates are rejected and the existing entry stays unchanged (2b), and a valid transaction is stored once with its audit entry. Payments are recorded, never executed.

## Code

- `backend/app/pricing_bookkeeping/ledger.py`:
  - `LedgerService(store).record(actor_id, TransactionInput)`
  - `transactions()`, newest first
  - `validate_transaction(...)`, the pure 2a checks
- `backend/app/pricing_bookkeeping/routes.py`, mounted in `app/main.py`:
  - `POST /api/finance/transactions` returns 201 for a new entry and 200 for a replayed retry. It returns 422 with `issues: [{field, message}]` for invalid data, and 409 for a duplicate or a reused request key. The duplicate response includes the `existing` entry.
  - `GET /api/finance/transactions`
- `check_decimal` in `quotation.py` is now public, so the quote and the ledger share one money check.

## Rules

- **Validation (2a).** Every invalid field is reported together:
  - kind is `Income` or `Expense`;
  - amount is a `Decimal` above 0 with at most 2 decimal places. The API takes it as a string, so there are no floats;
  - `source_type` is one of `Invoice`, `Receipt`, `Voucher` or `OrderPayment`;
  - `source_reference` is required, at most 200 characters and free of NUL characters. It is trimmed and stored in upper case, so `inv-001` and `INV-001` count as the same invoice;
  - an `OrderPayment` must name its order, so profitability (BAKE-34) can attribute every order payment;
  - `occurred_at` has a time zone and is not in the future (5 minutes allowed for clock differences);
  - any linked order or product exists.

  Rejections are logged with the fields.
- **Duplicates (2b, [R4]).** The business origin `(source_type, source_reference)` identifies a transaction, never the amount or date. A second entry with the same source is rejected, and the response points to the existing entry.
- **Retries.** An optional `request_key` per owner makes an HTTP retry safe:
  - the same key with identical details returns the original (`replayed`);
  - the same key with different details returns 409 and stores nothing (review of #12).
- **Concurrency.** Both rules are also unique constraints (`uq_transactions_source`, `uq_transactions_request_key`). If two submissions race, the database rejects the second, and the service reports it as a duplicate or a replay.

## Dependencies and deviations

- **Owner sign-in (BAKE-14).** `/api/finance` is one of BAKE-14's owner-only route families, where its middleware checks the session, the owner role and CSRF. Until #14 is merged, `routes.current_owner` rejects every request with 401, so the routes cannot be reached without sign-in. After #14 merges, `current_owner` becomes `app.auth.access.require_owner`, a one-line change.
- **Route path.** The plan said `/api/owner/transactions`. This uses `/api/finance/transactions` to match BAKE-14's route families. Both are owner-only.
- **Source types.** The list of source types is a Stage 2 refinement. The Stage 1 report does not list them, and a fixed list stops "invoice" and "Invoice" counting as different sources.
- No UI yet. The owner dashboard page can follow with BAKE-34 (profitability), which reads this ledger.

## Verification

- Unit tests (`tests/unit/test_ledger_validation.py`): every 2a rule, issues reported together, time zones, and routes refusing requests without an owner.
- PostgreSQL integration tests (`tests/integration/test_ledger.py`, T-UC06-REPLAY):
  - a duplicate leaves the ledger and the audit unchanged;
  - an invalid amount stores nothing;
  - the same amount with a different reference is accepted;
  - a request-key retry returns the original;
  - a reused request key with different details stores nothing;
  - links that don't exist are rejected;
  - two concurrent submissions of one invoice store it once;
  - the API returns 201, 200, 409 and 422.
