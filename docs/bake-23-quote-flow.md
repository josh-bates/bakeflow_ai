# BAKE-23: quote approval and customer acceptance

> **Status: service layer (#21, merged), routes and UI (this PR) complete.** The only follow-up is wiring in BAKE-22's explainer once #19 merges.

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

## Routes and UI

**Owner** (`backend/app/pricing_bookkeeping/quote_routes.py`, in the owner-only `/api/finance` family, CSRF checked on every POST, and each handler declares `require_owner`):

| Route | Result |
|---|---|
| `GET /api/finance/quotes` | Draft and Quoted orders, soonest collection first, each with whether it can be priced (a current stock check), its latest quote version and the owner's decision (Pending, Approved or Withheld) |
| `POST /api/finance/orders/{id}/quotes` | 201: version 1, with optional `additionalCosts: [{description, amount}]` |
| `POST /api/finance/quotes/{id}/versions/{v}/revise` | 201: a new version |
| `POST /api/finance/quotes/{id}/versions/{v}/approve` and `/withhold` | 200 for exactly that version; 409 with the reason for a superseded or already decided version |

**Customer** (`current_customer`; another customer's order is "not found", the same as a missing one):

| Route | Result |
|---|---|
| `GET /api/orders/{id}/quote` | `{quote}`: the approved, current version with only its total and decision (never the bakery's costs), or `null` |
| `POST /api/quotes/{id}/versions/{v}/accept` | Confirms the order (TX-ACCEPT); a retry returns `replayed: true` |
| `POST /api/quotes/{id}/versions/{v}/reject` | Records the rejection; the owner sees it and can revise |

Amounts are decimal strings both ways, so a JSON number or more than two decimal places is a 422.

**Pages.**
- The owner's **Pending approvals** page (previously a placeholder) lists each order being quoted. It shows "Calculate quote", the breakdown (ingredients, labour, packaging, additional costs, quoted price and target margin), the manual-review flag, the explanation or "Explanation unavailable", the decision, and the actions: Approve version N, Withhold and revise, and Revise as a new version with an optional extra cost.
- On the customer page, a **Quoted** order has "View quote". It shows the total with Accept and Decline, or says the quote is still being prepared.

**Explanation.** BAKE-22's explainer (#19) is not merged yet, so quotes are saved without an explanation and flagged for manual review (4a). Once #19 merges, `draft` and `revise` get `explain=` wrapping `QuoteExplainer.explain`, a small follow-up.

**Migration chain:** `bake23_quote_explanation` follows BAKE-32's `bake32_receipts`. This PR adds no migration.

## Review of #21 (Carl)

- **Locking order.** `approve`, `withhold`, `accept` and `reject` now lock the order **before** reading the latest quote version, then re-read it fresh. `revise` saves under the same order lock, so a revision can't commit in between and the owner can't approve a version that was just superseded. A store-level `StaleVersionError` on a customer's decision becomes the "revised quotation will follow" `QuoteError`. A regression test injects a revision at exactly that moment.
- **Alembic heads (done, 25 Oct).** #14 merged first, so `bake23_quote_explanation` now follows `bake14_sessions`, and the chain has a single head again.

## Second review of #21 (Carl)

- **Retried acceptance after scheduling.** A retry is now replayed whenever the order already records this quote version as accepted. That holds at every later stage too (Scheduled, InProduction, Completed after BAKE-21's release), not only while the order is Confirmed.
- **Cancelled orders.** `approve` and `withhold` are refused (`CONFLICT`) unless the order is Draft or Quoted. Once an order is Cancelled, its quote is hidden from the customer and cannot be accepted.
- **Withhold** stays a final decision for the version, matching architecture `QUOTE_WITHHELD` (agreed on #32). The quote page will label it "Withhold and revise".

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
  - a retried acceptance is replayed after the order moves on to Scheduled, InProduction or Completed;
  - a cancelled order's quote cannot be approved or withheld, and an approved one is hidden and cannot be accepted;
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

**Routes and UI (this PR):**
- `tests/integration/test_quote_routes.py` (PostgreSQL, real sign-in and CSRF):
  - the owner prices, approves (a double click is harmless) and the customer accepts, so the order is Confirmed, a retry is replayed, and the order leaves the queue;
  - withhold, refused re-approval, revise with a $5.00 gift box, approve version 2, and the customer rejects;
  - amounts must be decimal strings;
  - each side reaches only its own routes (401, 403, and 404 for another customer).
- `frontend/src/test/ApprovalsPage.test.tsx`:
  - pricing shows every figure and the manual-review flag;
  - approving sends exactly the shown version;
  - a revision with an extra cost, where an invalid amount is never sent;
  - the server's refusal reason is shown;
  - the customer accepts without ever seeing the bakery's costs;
  - "still preparing" is shown when no quote is approved.
