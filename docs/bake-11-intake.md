# BAKE-11: customer-confirmed draft orders

Implements Ziyi's UC-01 / AR-01 intake: SalesAgent interprets a customer request through BAKE-9; BAKE-8 bounds malformed/provider failures to three calls total; deterministic validation checks catalogue membership, whole positive quantities, future timezone-aware collection time, optional nonnegative budget, and explicit allergen disclosure. Missing customer facts produce an incomplete summary rather than attempts to invent facts. A customer can correct the original text and request a new summary.

## Dependencies and scope

This branch integrates BAKE-10 (#7), BAKE-7 (#8), BAKE-8 (#4), BAKE-9 (#5) and main's BAKE-6 skeleton, preserving their commits. These dependencies need teammate review/merge; only the intake, its UI integration, tests and the changes below belong to BAKE-11. Do not merge dependency implementations as unreviewed BAKE-11 work.

- `POST /api/order-requests {message}` returns `summary`, `issues`, and an optional signed `confirmationToken`. It does not save an Order.
- `POST /api/order-requests/confirm {confirmationToken}` revalidates the signed, customer-bound summary and writes an Order plus its AuditEntry through ValidatedRecordStore in one transaction. Response contains `orderId` and `status: Draft` only after commit. No stock or recipe changes occur.
- Repeat confirmation of the same token returns the same order. A PostgreSQL customer-row lock serialises concurrent confirmations. Summaries expire after 30 minutes and a process restart invalidates outstanding tokens; review a new summary then. This is a single-process prototype, not a distributed session design.
- `GET /api/orders/mine` filters by the server-supplied customer identity.
- Normal routes fail closed until BAKE-14 supplies `current_customer` and the session's `request.state.csrf_token`. No customer IDs are accepted from request JSON. The explicit local demo below is not a production login system.

## Model refinements

The BAKE-7 model lacked allergen disclosures and collection deadline semantics. A follow-on migration adds nullable `Order.allergen_concerns` (null means unknown; [] means explicitly no concerns) and `collection_kind` (`at` / `before`). Both appear in the audited snapshot. Existing orders remain unknown rather than being declared allergy-free. Coordinate these additions with BAKE-7's owner.

BAKE-16 follow-up questions and BAKE-17 owner exceptions are not implemented: missing allergen information cannot save; disclosed concerns or recipe customisation block confirmation and explicitly require owner review. This flow does not assert allergen safety or claim an owner-review request was queued. Themes remain supported. Resolved recipes are left unset for the owning downstream service.

A date without a year uses its next occurrence in Australia/Sydney and the summary explicitly displays the full date/year for confirmation. “Before 12:00” remains a deadline, not an exact appointment. The Jira October 2 example therefore shows October 2 of the next year when run after that date. Input limits: 4,000 characters, 30 line items, quantity 1–10,000.

## Local demonstration

Use the repository's PostgreSQL setup and seed commands first. From the repository root build the frontend (`pnpm --dir frontend build`). From `backend/`, with the local database URL and seed passwords configured:

```sh
alembic upgrade head
python -m app.seed
python -m app.sales.demo
```

Open http://127.0.0.1:4176. This explicit development-only entry uses the single seeded customer, checks loopback requests, uses CSRF protection, and runs Mock mode. It writes to the configured demo database. Normal `app.main` does not enable this identity shortcut.

Mock accepts these canned examples only (not arbitrary natural language):

- `24 vanilla birthday cupcakes for collection on 2 Oct before 12:00, no allergy concerns`
- `24 vanilla birthday cupcakes, no allergy concerns`
- `24 vanilla birthday cupcakes for collection on 2 Oct before 12:00`
- `24 croissants for collection on 2 Oct before 12:00, no allergy concerns`

Other inputs fail after the bounded attempts rather than producing a fabricated order. `BAKEFLOW_LLM_MODE=live` uses the existing provider environment configuration in the normal app after account integration; the mock demo entry deliberately disallows live mode.

## Verification and remaining gates

Unit tests use SQLite for transaction rollback, record snapshots and HTTP checks; that does not verify PostgreSQL row locking. PostgreSQL integration tests cover migrations, persisted drafts/audits, unchanged inventory, rollback and concurrent duplicate confirmation. Run them using the documented disposable BAKE-7 test database and `BAKEFLOW_TEST_DATABASE_URL`; never point the test suite at demo or real data because it resets the schema. The backend CI job provisions its own throwaway PostgreSQL.

Frontend tests cover summary-before-save, incomplete requests, correction, confirmation failure and retry, alongside BAKE-10 navigation/session coverage. Full acceptance still requires PostgreSQL tests, browser walkthrough with screenshot evidence, account integration and a reviewed PR. No claim of completed Jira DoD or a confirmed sale is made.
