# BAKE-18 — customer order tracking (UC-02)

Each customer order row has a **Track order** button. The panel shows the stored
status, a Sales Agent explanation, and the order version used. **Refresh status**
reads a new snapshot and updates the list row. Tracking never changes order stages,
inventory, payments or approvals.

## Contract and access control

`GET /api/orders/{order_id}/tracking` requires the ordering customer's signed-in
session. The backend filters by both order ID and authenticated customer ID before
calling the LLM. Another customer's order, a missing ID and a malformed ID return
the same generic 404. Anonymous requests receive 401; owner accounts cannot use
this customer-only endpoint (403). The existing owner-authorised general detail
endpoint from BAKE-14 remains separate for bakery operations.

The response contains `id`, `status`, `version`, `requiredAt`, `explanation`, and
`explanationAvailable`. Status and version come from one authorised database
snapshot; the transaction closes before the provider call. They are never sourced
from AI output. The explanation concerns that snapshot; refresh obtains a newer
version. Database failures return a generic 503 rather than a fabricated status.

## Grounded Sales Agent explanation

The Sales Agent chooses between approved explanations for each of the seven
shared order stages. It must echo the recorded stage and copy a permitted
explanation exactly. Validation rejects mismatched stages, unapproved text, extra
keys and malformed replies. This design refinement prevents invented prices,
collection promises, payments, refunds or allergy claims. The provider sees the
stage and approved text only, not customer identity, order ID or order contents.

WorkflowService owns the retry budget: one attempt and at most two retries. If
none succeeds, including provider configuration/connection failures, the endpoint
still returns 200 with the recorded status, `explanationAvailable: false` and an
explanation-unavailable message. Explanation failure does not write a business
audit entry or change the order. The workflow's existing in-memory failure event
is not persisted as a business mutation.

Mock mode returns a valid approved explanation. Live mode uses the existing
server-side gateway and private configuration; a live provider was not used to
validate this change. There are no new dependencies or migrations.

## Verification and walkthrough

Tests cover all seven stages, invalid/mismatched/invented explanations, recovery
within the retry budget and timeout fallback. PostgreSQL HTTP tests check denial
before provider calls, identical unknown/unauthorised responses, owner/other
customer rejection, recorded status surviving explanation failure, and unchanged
order versions and audit records. UI tests cover fallback and refreshed status.

Sign in as the seeded customer, save a draft through UC-01, then select Track order.
The panel displays Draft and explains that this is not a confirmed sale. Refresh
checks the saved record again. This branch is based on main and does not depend
on the unmerged BAKE-16 PR.

Review compatibility: tracking imports `RecordRef` directly from `app.shared.refs`
and converts the wire-format order ID to `uuid.UUID` before entering the workflow.
This works with both the current workflow and BAKE-41 (#25). Provider-failure
fixtures use the gateway's `LLMCallFailed` contract, which #25 retries explicitly.
