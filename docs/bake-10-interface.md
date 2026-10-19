# BAKE-10 interface handoff

## Scope and contribution

Ziyi owns BAKE-10 following the team's reassignment from Carl. Implements the
Stage One interface layer (sections 3 and 14), customer intake entry and own-order
view (AR-01 / UC-01 / UC-02), and owner navigation. Owner pages explicitly remain
placeholders, as allowed by the Jira acceptance criteria. No price, stock,
approval or scheduling calculations run in the browser.

This handoff records a **proposed HTTP contract** because BAKE-6/11/14 had not
provided endpoints on `main`. Align these URLs and DTOs with their owners before
merge. Only `frontend/src/api/client.ts` contains the JSON endpoint integration.

## Proposed endpoints

All endpoints are same-origin. Identity comes from a server-managed session
cookie, never a browser-supplied customer ID or role selector. Production cookies
must have appropriate HttpOnly/Secure/SameSite settings. The backend must verify
CSRF tokens and Origin for writes, apply input limits, enforce authorisation,
and deduplicate retry keys. Frontend route guards are convenience only.

| Method/path | Response / purpose |
| --- | --- |
| `GET /api/session` | `{ "user": { "displayName": "Zoe", "role": "Customer" }, "mode": "mock", "csrfToken": "…" }`. Role is `Customer` or `Owner`; mode is `mock` or `live`. 401 for no valid session. |
| `GET /api/auth/login` | Browser entry to backend-owned sign-in. Redirect or render the authentication interface; this UI does not store credentials. |
| `GET /api/orders/mine` | `{ "orders": [{ "id": "ORD-001", "summary": "12 vanilla cupcakes", "status": "Draft", "requiredAt": "2026-10-25T03:00:00Z" }] }`. Only orders owned by the authenticated customer. `requiredAt` may be null; otherwise use an ISO timestamp with a timezone. |
| `POST /api/order-requests` | JSON `{ "message": "12 vanilla cupcakes…" }`, `X-CSRF-Token` and `Idempotency-Key` headers. Return 202 with `{ "requestId": "REQ-001", "message": "Request received. Further details will be reviewed before saving a draft." }` after backend acknowledgement. Receipt is not a saved Draft or confirmed sale. |

Return JSON and appropriate 401/403/422/429/5xx statuses. No raw server exception
is rendered in the UI. The UI retains text after failure, disables repeat clicks
while sending, and reuses a retry key for unchanged text after uncertain delivery.
Backend persistence and the UC-01 clarification/summary-confirmation workflow are
BAKE-11, not implemented by this shell. A request must not bypass those gates.
There is no polling or automatic write retry.

## Design refinements / deviations

- React/TypeScript/Vite follows the working Stage Two choice, not a technology
  mandated by Stage One. Initialising `frontend/` here overlaps the frontend
  portion of BAKE-6 because no skeleton had landed; Carl should reuse it rather
  than generate a second application.
- Proposed endpoint paths, JSON field names and CSRF/retry headers are interface
  refinements requiring backend agreement, not changes to business rules.
- Hash routes are a deployment simplification: all views use one built HTML
  entry. The final app is still one backend-served browser interface.
- Test fixtures provide HTTP responses solely for UI tests/screenshots. Their
  fixed role is **not authentication**, and their intake receipt is **not storage**.
- Owner placeholders are explicitly allowed by BAKE-10 and do not claim completed
  inventory, approvals, orders or schedules functionality.

## Acceptance evidence and remaining gates

Automated frontend tests cover own-order endpoint selection, intake receipt,
failed writes/retry keys, repeat-click prevention, failed reads and retry,
signed-out/expired sessions, role navigation, malformed payloads and the mode label.
TypeScript checking and a production build are required before PR review.

Local validation: all 11 frontend tests passed, and TypeScript checking plus the
production build passed. Browser preview/screenshot capture was attempted but
blocked because the browser tool could not verify its administrator-enforced
security policy. No browser walkthrough or screenshots are claimed as completed.

Screenshots and walkthrough evidence belong in `docs/evidence/bake-10/` and must
be attached to the Jira issue. Fixture evidence must be identified as such.
**Do not mark BAKE-10 Done** until BAKE-6 serves the build, BAKE-11/14 endpoints
are integrated and tested, screenshots are attached to Jira, automated checks
pass, and a teammate reviews and merges the PR with BAKE-10 in its title.

## Interview explanation

“The UI has two roles. It asks the backend who is signed in and only displays the
appropriate navigation. Customer requests go to a backend endpoint, and the
orders list comes from an endpoint that must enforce ownership. The browser
does not calculate prices or stock and does not call the LLM. Owner modules are
labelled placeholders. We build the frontend once and serve its static files
from the same backend.”
