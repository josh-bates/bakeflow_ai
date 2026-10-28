# BAKE-23: quote approval and customer acceptance

> **Status: service layer complete, using Angelina's `ApprovalService` (BAKE-15, merged in #24).**
> Routes and UI come next, in a separate PR once this one merges. BAKE-23 stays **In Progress** until then.

Implements UC-05 steps 1-3 and 5-9 (AR-03): save a versioned quote draft for the owner; the owner approves it, withholds approval or revises it; the customer sees only the approved current version; and acceptance confirms the order in one transaction.

## What is built (`backend/app/pricing_bookkeeping/`)

- **Approvals use BAKE-15's `ApprovalService`** (`app/shared/approvals.py`) with `ApprovalKind.QUOTATION`:
  - `draft` and `revise` call `request(session, ref, ...)` in the same unit of work as the quote version, so the two are saved together or not at all.
  - `approve` and `withhold` look up that version's request (`request` is idempotent and returns it) and call `decide(session, request_id, ...)`. A decision is final for its version: approving a withheld version is refused with `CONFLICT`, and the owner revises instead (6b). A repeated approval of an approved version returns the quote and changes nothing.
  - `customer_view` and `accept` use `is_approved(session, ref, QUOTATION)`, which is true only for the current version.
- **`quotes.py`: `QuoteService(store)`** (the `ApprovalService` can be passed in)
  - `draft(order_id, owner_id, additional_costs, explain)` (TX-QUOTE-DRAFT):
    - **Inputs:** builds `CostingInputs` from the order items, the order's **current-version** shopping list (UC-03), ingredient unit costs and the owner's settings.
    - **Calculation and explanation:** calculates with `FinanceService`, then runs the explanation hook **outside** any transaction.
    - **Save:** in one unit of work, re-checks that the order hasn't changed and saves Quote v1 together with its approval request.
  - `revise(quote_ref, ...)` (6a, 8a) saves a new version with its own request. The old version's approval no longer applies.
  - `approve` and `withhold` (6, 6b) act only on the latest version. Approval moves the order from Draft to Quoted **without changing `Order.version`** (review of #12).
  - `customer_view` shows only the latest version, and only once it is approved for the current order version (7a). Another customer's order gives the same "not found" as a missing one.
  - `accept` (TX-ACCEPT, 9, 9a) re-checks that the version is current, approved and priced from the current order version. It then records `customer_decision=Accepted` and confirms the order **in one transaction**. A retry after success returns the earlier result (`replayed`) without recording anything twice.
  - `reject` (8a) records the decision. The owner sees it through `latest(order_id)` and can revise.
  - Errors use the architecture §6.2 codes (`NOT_FOUND`, `VALIDATION_FAILED`, `STALE_VERSION`, `APPROVAL_REQUIRED`, `CONFLICT`), and a refused step saves nothing.
- **`backend/app/sales/orders.py`: `OrderService`**, which the plan agreed I'd add for **Ziyi to review**.
  - `mark_quoted` (Draft to Quoted) and `confirm` (Quoted to Confirmed, bound to the accepted quote).
  - Both follow BAKE-8's `ALLOWED_MOVES` and commit with `expected_version`, without bumping the version.
- **Migration `bake23_quote_explanation`:** a nullable `quotes.explanation` column, which BAKE-22 deferred to this story. NULL means no usable explanation, so the quote is flagged for manual review (4a).

## Next (separate PRs)

| Dependency | Then |
|---|---|
| BAKE-22 (#19): QuoteExplainer | Pass `explain=lambda calc, units: ...` wrapping `QuoteExplainer.explain`. Until then, drafts get no explanation and are flagged for manual review, which is correct 4a behaviour. |
| Routes (BAKE-14's sign-in is merged) | Owner routes: `POST /api/finance/orders/{id}/quotes` and `/api/finance/quotes/{id}/versions/{v}/approve`, `…/withhold` and `…/revise`. Customer routes: `GET /api/orders/{id}/quote` and `POST …/accept` and `…/reject`. |
| UI | Owner Approvals page (figures, flags, explanation or "manual review", and actions) and the customer quote card with Accept and Reject. |

**Migration chain:** `bake23_quote_explanation` follows BAKE-14's `bake14_sessions` (re-pointed after #14 merged), so Alembic has one head.

## Review of #21 (Carl)

- **Locking order.** `approve`, `withhold`, `accept` and `reject` now lock the order **before** reading the latest quote version, then re-read it fresh. `revise` saves under the same order lock, so a revision can't commit in between and the owner can't approve a version that was just superseded. A store-level `StaleVersionError` on a customer's decision becomes the "revised quotation will follow" `QuoteError`. A regression test injects a revision at exactly that moment.
- **Alembic heads (done, 25 Oct).** #14 merged first, so `bake23_quote_explanation` now follows `bake14_sessions`, and the chain has a single head again.

## Deviations

- **Order status:** the order is `Quoted` while an approved quote awaits the customer. This matches BAKE-8's merged `ALLOWED_MOVES`, whereas architecture §4.1.1 goes straight from Draft to Confirmed. Agreed in the review of #12.
- **No explainer:** a draft with no explainer is saved without an explanation and flagged for manual review (4a), rather than being refused.

## Verification

`tests/integration/test_quote_flow.py` has 19 PostgreSQL tests using the real `ApprovalService`. These run in CI.
- **Seeded order:** 24 vanilla cupcakes, checked by hand, give 14.96 + 45.00 + 6.00 = 65.96, and 65.96 ÷ 0.7 = **94.23**. An approval request is saved with the draft.
- **Drafting:**
  - no explanation means manual review;
  - additional costs are included;
  - an order without a current stock check is refused;
  - if the request fails, no quote is saved;
  - an order gets one quote, then revisions.
- **Owner decisions:**
  - the customer sees only an approved quote; withholding keeps it hidden, and a withheld version must be revised before approval;
  - the approval is recorded once by `ApprovalService` (kind `Quotation`, this version, the owner, one `approval.approved` audit entry), and a repeated approval changes nothing;
  - approval keeps `Order.version`;
  - approving a superseded version is refused;
  - a revised version is hidden until it is approved again.
- **Customer decisions:**
  - accepting confirms the order;
  - **accepting a superseded version does not confirm it**;
  - an unapproved quote cannot be accepted;
  - **if `OrderService.confirm` fails, neither the acceptance nor the confirmation is stored, and a retry then succeeds exactly once**;
  - a rejection is recorded and can be followed by a revision;
  - another customer gets "not found".
