# BAKE-14 — sign-in and access control

Run the README's database migration and seed steps, then start the normal app.
Use `customer@bakeflow.test` or `owner@bakeflow.test` with the passwords supplied
in `BAKEFLOW_SEED_CUSTOMER_PASSWORD` / `BAKEFLOW_SEED_OWNER_PASSWORD`.
There is no registration, role selector, or default password.
The existing seed command does not change passwords of existing accounts.

The browser now shows a sign-in form, loads the server-selected customer/owner
workspace, and provides Sign out. The bakery styling and order intake are retained.

For the optional local PostgreSQL launcher, run `python scripts/run_local_demo.py`
from `backend/` with its optional pgserver dependency installed. It uses a separate
`.local/auth-demo` database and writes generated credentials to the gitignored,
owner-readable `.local/demo-accounts.txt` on first seed. It never resets the older
`.local/postgres-demo` database. Build the frontend before starting this launcher.
The former single-customer identity shortcut has been removed.

## API contract for other modules

- `POST /api/auth/login`: JSON `{email, password}`, header `X-Requested-With: BakeFlow`;
  returns 204 and a new HttpOnly, SameSite=Lax cookie. Invalid credentials return
  the same 401 for unknown email and wrong password.
- `GET /api/session`: display name, role, Mock/live mode, CSRF token. No account ID
  or role is trusted from browser input.
- Every private `/api/` request requires a valid session. Health and login are public.
  Unsafe methods additionally require `X-CSRF-Token` from `/api/session`.
  Cross-origin writes are rejected; frontend and API must share one origin.
- `POST /api/auth/logout`: CSRF required, revokes the database session and clears cookie.
- `GET /api/orders/mine`: signed-in customer's orders only.
- `GET /api/orders/{id}`: customer ownership check, owner may read any order.
  Another customer's order, nonexistent ID and malformed ID all return identical 404.
- Owner handlers must use `Depends(require_owner)` from `app.auth.access`.
  It returns the authenticated identity, including `user_id` for `store.commit`'s actor.
  `/api/owner`, `/api/approvals`, `/api/inventory`, `/api/finance`, `/api/marketing`
  and `/api/schedules` are also guarded as owner-only route families.
  A handler under another path (e.g. `/api/orders/{id}/approve`) must explicitly
  declare the dependency. Do not infer permission from the UI or supplied role.

Approval transitions and approval writes belong to BAKE-15. Tests install a test-only
approval handler to prove a customer cannot execute it and the owner can; no mock
production approval endpoint is added by BAKE-14.

## Design refinement and deployment

The added `auth_sessions` infrastructure table stores only a SHA-256 digest of a
random 256-bit token, account reference and eight-hour expiry. It is outside
versioned business records, avoiding credentials in business audit snapshots.
Passwords continue using BAKE-7's scrypt hashing. Sessions survive app restarts and
are shared across workers; roles are re-read on every request. Login rotates the
previous session and logout revokes it. Expired rows are ignored; periodic cleanup
is not implemented in this prototype.

Migration `bake14_sessions` follows main's `bake11_disclosures`; there is one head.
Use HTTPS for deployment: cookies are Secure outside development/test. The local
launcher binds loopback for HTTP demonstrations. Configure a trusted proxy's host
and scheme correctly if terminating HTTPS upstream.
The login throttle is 20 attempts/minute per peer per process; it is a prototype
limit, not a distributed brute-force protection service.

## Verification

PostgreSQL integration tests exercise seeded login, token rotation, persistent
sessions, logout revocation, expiry, CSRF, cross-origin rejection, credential
redaction, two-customer order isolation and owner-only execution. Frontend tests
exercise sign-in, rejected credentials, sign-out and existing intake behavior.

Local validation on 21 October 2026: Python 3.13 full backend suite passed
251 tests on a disposable PostgreSQL 16.2 instance, including Alembic model
consistency. The subsequent authenticated-intake regression is included in the
auth suite. Frontend: 16 tests passed and production build succeeded.
Browser smoke check: the real server serves the sign-in form at port 4176.
Team CI's PostgreSQL 17 run and peer review remain required before merge.
