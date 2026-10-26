# BAKE-11: customer-confirmed draft orders

Implements Ziyi's UC-01 / AR-01 intake: SalesAgent interprets a customer request through BAKE-9; BAKE-8 bounds malformed/provider failures to three calls total; deterministic validation checks catalogue membership, whole positive quantities, future timezone-aware collection time, optional nonnegative budget, and explicit allergen disclosure. Missing customer facts produce an incomplete summary rather than attempts to invent facts. The agent must quote an allergen disclosure from the actual request. Empty concerns additionally require an explicit phrase such as “no allergy concerns”, “no allergies”, or “allergies: none”; otherwise confirmation stays blocked. A customer can correct the original text and request a new summary.

## Dependencies and scope

This branch integrates BAKE-10 (#7), BAKE-7 (#8), BAKE-8 (#4), BAKE-9 (#5) and main's BAKE-6 skeleton, preserving their commits. BAKE-6 and BAKE-7 are now merged; BAKE-10, BAKE-8 and BAKE-9 still need teammate review/merge; only the intake, its UI integration, tests and the changes below belong to BAKE-11. Do not merge dependency implementations as unreviewed BAKE-11 work.

- `POST /api/order-requests {message}` returns `summary`, `issues`, and an optional signed `confirmationToken`. It does not save an Order.
- `POST /api/order-requests/confirm {confirmationToken}` revalidates the signed, customer-bound summary and writes an Order plus its AuditEntry through ValidatedRecordStore in one transaction. Response contains `orderId` and `status: Draft` only after commit. No stock or recipe changes occur.
- Repeat confirmation of the same token returns the same order. A PostgreSQL customer-row lock serialises concurrent confirmations. Summaries expire after 30 minutes. Configure the same private `BAKEFLOW_APP_SECRET` for all workers and restarts; only development may fall back to a temporary key that invalidates summaries on restart. Authentication is now supplied by BAKE-14.
- `GET /api/orders/mine` filters by the server-supplied customer identity.
- BAKE-14 supplies `current_customer` and the session's `request.state.csrf_token`. No customer IDs are accepted from request JSON. The local demo uses the same sign-in flow.

## Model refinements

The BAKE-7 model lacked allergen disclosures and collection deadline semantics. A follow-on migration adds nullable `Order.allergen_concerns` (null means unknown; [] means explicitly no concerns) and `collection_kind` (`at` / `before`). Both appear in the audited snapshot. Existing orders remain unknown rather than being declared allergy-free. Coordinate these additions with BAKE-7's owner.

BAKE-16 follow-up questions and BAKE-17 owner exceptions are not implemented: missing allergen information cannot save; disclosed concerns or recipe customisation block confirmation and explicitly require owner review. This flow does not assert allergen safety or claim an owner-review request was queued. Themes remain supported. Blank customisation and the exact marker `standard` (case-insensitive, with surrounding whitespace ignored) are normalised to null before validation and persistence. Other customisations still require owner review, as do disclosed allergen concerns even on standard items. Resolved recipes are left unset so BAKE-12 can use the product recipe for standard items only. The migration remains chained to BAKE-7 revision `06a90cacc254`.

A date without a year uses its next occurrence in Australia/Sydney and the summary explicitly displays the full date/year for confirmation. “Before 12:00” remains a deadline, not an exact appointment. The Jira October 2 example therefore shows October 2 of the next year when run after that date. Input limits: 4,000 characters, 30 line items, quantity 1–10,000.

## Local demonstration

Use the repository's PostgreSQL setup and seed commands first. From the repository root build the frontend (`pnpm --dir frontend build`). From `backend/`, with the local database URL and seed passwords configured:

```sh
alembic upgrade head
python -m app.seed
python -m app.sales.demo
```

Open http://127.0.0.1:4176. This development-only entry checks loopback requests and runs Mock mode. Sign in using a seeded account; it writes to the configured demo database.

Mock accepts these canned examples only (not arbitrary natural language):

- `24 vanilla birthday cupcakes for collection on 2 Oct before 12:00, no allergy concerns`
- `24 vanilla birthday cupcakes, no allergy concerns`
- `24 vanilla birthday cupcakes for collection on 2 Oct before 12:00`
- `24 croissants for collection on 2 Oct before 12:00, no allergy concerns`

Other inputs fail after the bounded attempts rather than producing a fabricated order. `BAKEFLOW_LLM_MODE=live` uses the existing provider environment configuration in the normal app after account integration; the mock demo entry deliberately disallows live mode.

## Verification and remaining gates

Unit tests use SQLite for transaction rollback, record snapshots and HTTP checks; that does not verify PostgreSQL row locking. PostgreSQL integration tests cover migrations, persisted drafts/audits, unchanged inventory, rollback and concurrent duplicate confirmation. Run them using the documented disposable BAKE-7 test database and `BAKEFLOW_TEST_DATABASE_URL`; never point the test suite at demo or real data because it resets the schema. The backend CI job provisions its own throwaway PostgreSQL.

Frontend tests cover summary-before-save, incomplete requests, correction, confirmation failure and retry, alongside BAKE-10 navigation/session coverage. Full acceptance still requires PostgreSQL tests, browser walkthrough with screenshot evidence, account integration and a reviewed PR. No claim of completed Jira DoD or a confirmed sale is made.


### Optional local PostgreSQL launcher (without Docker)

Installed locally for development; the team's Compose deployment is unchanged.

```sh
# From backend/, with the frontend already built:
.venv/bin/python -m pip install -r requirements-local-demo.txt
.venv/bin/python scripts/run_local_demo.py
```

This launcher runs a native PostgreSQL 16.2 server, applies migrations, seeds only an empty database, and starts the Mock demo at http://127.0.0.1:4176. Since BAKE-14, new demo data persists under the ignored `.local/auth-demo/` directory; the old database is left untouched. Stop with Ctrl+C; restarting preserves drafts. Generated local account credentials are in `.local/demo-accounts.txt` (owner-readable and gitignored). Sign-in is required. Team CI uses PostgreSQL 17.

### Validation performed 19 October 2026

- 181 backend tests passed against a newly created native PostgreSQL test database, including all integration tests; no skipped tests.
- 14 frontend tests and production build passed.
- Browser walkthrough on the persistent local PostgreSQL demo: Jira input → summary → confirm → Draft reference → refresh retains the saved order. Missing allergen disclosure disables confirmation; correcting the input enables a new summary.
- Screenshot: [summary before confirmation](evidence/bake-11/summary-before-confirmation.png). This captures Mock extraction with real local database persistence, not a live LLM test. Jira attachment and team review remain outstanding.

BAKE-14 now supplies authentication; see [the sign-in contract](bake-14-auth.md). Live provider extraction has not been exercised; no API key is required for these Mock tests.


### PR review follow-up

- Signing reads the private `BAKEFLOW_APP_SECRET`; all workers must use the same value. Missing keys are refused outside development, and configured keys must contain at least 32 bytes. Development fallback logs its restart/worker limitation without exposing the key. Alembic preserves application loggers when configuring migration logging.
- CI uses Python 3.13, matching Docker. The optional native PostgreSQL helper's Windows/Python 3.13 compatibility limitation is documented in README; Compose remains supported.
- The local demo denies requests with no client address rather than raising an exception.
- Explicit no-concern statements can end with “thanks” or “thank you”. Extra qualifications such as “except peanuts” still block confirmation; the source-quote requirement is unchanged.

Review fixes validated with Python 3.13 and a fresh PostgreSQL database: all 205 backend tests passed, none skipped.
