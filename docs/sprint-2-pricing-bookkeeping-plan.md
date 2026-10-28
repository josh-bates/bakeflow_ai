# Sprint 2 plan: Pricing and Bookkeeping (Josh)

Implementation plan for Josh's Sprint 2 stories ("Confirmed order", 20–26 October 2026, ends Monday 26 October 17:00): **BAKE-22** quote explanation (3 pts), **BAKE-23** owner approval and customer acceptance (5), **BAKE-24** ledger (3) and **BAKE-27** CI (2), 13 points in total. **BAKE-34** profitability is the stretch goal. This is a plan, not implemented work. Each story still goes through its own reviewed PR.

Sprint goal served: in the browser, a signed-in customer's validated draft gets a deterministic quote, the owner approves it and the customer accepts, and the order becomes Confirmed.

## Starting point (main at `8c6eba6`)

**Already in place:**
- `FinanceService.calculate_quote` in `backend/app/pricing_bookkeeping/quotation.py` (BAKE-13).
- ORM `Quote` in `backend/app/shared/records/finance.py`: row-versioned, and only `customer_decision` changes in place.
- ORM `Transaction`, with the `uq_transactions_source` and `uq_transactions_request_key` constraints.
- The `ApprovalRequest` model.
- `ValidatedRecordStore.unit_of_work`, `commit` and `load` in `backend/app/shared/store.py`.
- `WorkflowService.run_ai_task(..., is_usable)` with `MAX_ATTEMPTS = 3`, and `ALLOWED_MOVES` Draft → Quoted → Confirmed, in `backend/app/workflow/order_flow.py`.
- `MockLLMClient` and `parse_structured_reply` in `backend/app/llm/gateway.py`.
- Patterns to follow: the `SalesAgent` prompt and parse code, `sales/fixtures.py`'s `mock_for()`, and `make_router(settings, *, store, client_factory)`.
- BAKE-12's `ShoppingList`.
- Seed settings: labour rate 30.00/h, target margin 0.30.

**Not built yet:**
- `ApprovalService` (BAKE-15, Angelina).
- Owner/customer authentication (BAKE-14, Ziyi).
- `OrderService.confirm`.
- `PricingBookkeepingAgent`.
- Somewhere to store the quote explanation.
- A lint step in CI.
- Protection on `main`.

## 1. Order of work

| Days | Story | Reason |
|---|---|---|
| Tue 20 – Fri 23 | BAKE-27 CI: **done 23 Oct** (#13, ruleset on `main`, #17) | No dependencies, and it helps every other PR this sprint |
| Fri 23 – Sat 24 | BAKE-24 ledger | Needs only the existing `Transaction` table and the store |
| Sat 24 – Sun 25 | BAKE-22 explanation | Needs only `QuoteCalculation` and the LLM gateway |
| Once BAKE-14 and BAKE-15 merge | BAKE-23 approval, acceptance and UI | Blocked. BAKE-14 is approved (#14) but not merged, and BAKE-15 has not started. If BAKE-15 has not landed by Saturday, BAKE-23 carries over to Sprint 3 |
| If time allows | BAKE-34 deterministic profit (stretch) | Needs the BAKE-24 ledger |

*Updated 23 October: dates moved to reflect actual progress.*

Each story gets one small PR with a named reviewer, and no PR is built on top of another unmerged one (Sprint 1 retro actions).

## 2. BAKE-27 CI (done)

This section describes what was built, which differs slightly from the original plan. Details are in [bake-27-ci.md](bake-27-ci.md).

- **Lint.** `ruff==0.17.1` is pinned in `backend/requirements-dev.txt`. The pin went into the same PR as the workflow change (#13), not a separate one, because the two only make sense together. `backend/pyproject.toml` selects `E4`, `E7`, `E9` and `F` **explicitly**. Ruff 0.17's own defaults are broader and flagged 113 style issues, so they are not used.
- **Workflows.** `.github/workflows/backend.yml` runs `ruff check .` before pytest. Both workflows run on every PR with no path filters, and cancel superseded runs with `concurrency`. The frontend workflow is now named "Frontend".
- **Branch protection.** `main` is protected by the "Protect main branch" ruleset: the `test` and `check` checks with the branch up to date, one approving review, and no bypass. The manual failing-test check is closed PR #17.

## 3. BAKE-24 ledger (UC-06 steps 1-3)

New module: `backend/app/pricing_bookkeeping/ledger.py`.

**Recording:** `LedgerService(store).record(owner_id, TransactionInput)`
- **Validation (2a):** kind is Income or Expense; amount is a `Decimal` above 0 with at most 2 decimal places (reusing the checks in `quotation.py`); `source_type` and `source_reference` are not empty; `occurred_at` is timezone-aware; any referenced order or product exists. A failed check returns the fields to fix and logs the rejection.
- **Storing:** each transaction is saved once with `store.unit_of_work()` and `commit(..., action="finance.transaction_recorded")`.
- **Duplicates (2b):** a `RecordValidationError` on `uq_transactions_source` or `uq_transactions_request_key` means a duplicate. The service then opens a new unit of work, loads the existing row and returns it as a duplicate result. The ledger stays unchanged. Resending the same request key with **identical** content returns the original entry. The same key with **different** content (amount, kind, reference, date or links) is a client error: the service returns a conflict (HTTP 409) and stores nothing, so the bug is not silently hidden.
- Payments are recorded only, never executed.

**Routes**
- `POST /api/owner/transactions` and `GET /api/owner/transactions`.
- Both sit behind a `current_owner` dependency, using the same override pattern as `sales/routes.py:current_customer`. BAKE-14 replaces it with real authentication.

**Tests**
- Unit tests for validation.
- PostgreSQL integration tests (T-UC06-REPLAY):
  - a duplicate leaves the ledger unchanged;
  - resending a request key with identical content returns the original;
  - reusing a request key with different content returns 409 and stores nothing;
  - an invalid amount is rejected;
  - a different reference with the same amount is accepted.

## 4. BAKE-22 quote explanation (UC-05 step 4, 4a)

New files: `backend/app/pricing_bookkeeping/agent.py` and `fixtures.py`.

**Agent**
- `PricingBookkeepingAgent(client: ILLMClient).explain_quote(calc) -> FinancialExplanation`.
- `FinancialExplanation` is a Pydantic model with `extra="forbid"` and fields `narrative`, `referenced_amounts[{label, amount}]` and `warnings`.
- The prompt is built the same way as `SalesAgent`'s, with a `PROMPT_VERSION` constant.

**Allowed figures.** The checker accepts the calculated money figures (material, labour, packaging, additional, subtotal, total and profit). It also accepts a short list of **derived values**, which are calculated deterministically and given to the agent in the prompt:
- price per unit, as total ÷ total units at 2 dp, for single-product orders only;
- labour hours, as `total_labour_minutes` ÷ 60 at 2 dp;
- target and realised margin as percentages.

The prompt tells the agent to quote only figures from that list and never to calculate its own. `raw_material_cost` is not offered, so only the rounded material cost can appear. This keeps the Mock fixtures and the checker consistent.

**Consistency check:** `check_explanation(explanation, calc)` is deterministic.
- Every referenced amount must equal the figure its label names.
- Every money amount, hour figure or percentage in the narrative must be in the allowed list.
- It is the `is_usable` function passed to `WorkflowService.run_ai_task`, which allows 3 attempts.

**Failure (4a):** if the attempts run out (`AIOperationFailed`), the quote is saved with no explanation and `requires_manual_review=True`. The figures never change.

**Storage:** a migration chained to the current Alembic head adds a nullable `quotes.explanation` column (Text).

**Mock fixtures:** a valid reply built from the calculation (including a per-unit price and labour hours), plus a wrong amount, an invented number, a self-derived figure that is not on the list, malformed JSON and a timeout.

**Tests**
- A wrong amount is rejected.
- After 3 failed attempts the quote is flagged for manual review.
- The figures are identical with and without an explanation.

## 5. BAKE-23 approval and acceptance (UC-05 steps 5-9)

New module: `backend/app/pricing_bookkeeping/quotes.py` (`QuoteService`).

**Drafting:** `draft(order_id, owner, additional_costs)`
- Requires a Draft order and a latest `ShoppingList` whose `source_order_version` equals the current order version.
- Builds `CostingInputs` from the order items with their `Product`, the shopping-list requirements with each `InventoryItem.unit_cost`, and `BakerySettings`.
- Calculates the quote, then gets the explanation outside any database transaction.
- One unit of work then saves Quote version N together with `ApprovalService.request(ref)` (TX-QUOTE-DRAFT).

**Owner actions**
- `revise` saves a new row, and the old approval no longer applies.
- `approve` and `withhold` use `ApprovalService.decide`. Approval moves the order from Draft to Quoted. That status change is committed with `expected_version` and **without** `revise=True`, so `Order.version` does not change. Otherwise the approved quote's `source_order_version` and the shopping list would no longer match the order, and every acceptance would be refused.

**Customer view:** `customer_view` shows only the latest version, and only once it is approved.

**Acceptance:** `accept` runs as one unit of work (TX-ACCEPT).
- Locks the order and reloads the latest quote.
- Checks the version is current and approved, matches the order's `source_order_version`, and belongs to the customer.
- If the order is already Confirmed with this quote, it returns the earlier result, so a retry is safe.
- Otherwise it commits `customer_decision=Accepted`, then `OrderService.confirm` sets the order to Confirmed with `accepted_quote_id` and `accepted_quote_version`.

**Rejection (8a):** `reject` records the decision, and the owner sees it on the Approvals page.

**Routes**
- Owner:
  - `POST /api/owner/orders/{id}/quotes`
  - `POST …/quotes/{qid}/versions/{v}/approve`, `…/withhold` and `…/revise`
- Customer:
  - `GET /api/orders/{id}/quote`
  - `POST …/accept` and `…/reject`, with CSRF checks.

**UI**
- Owner Approvals page: the cost breakdown, review flags, the explanation (or "Explanation unavailable — manual review"), and the Approve, Withhold and Revise actions.
- Customer page: the approved quote with Accept and Reject buttons.
- Vitest tests for both.

**Tests (PostgreSQL)**
- Accepting a superseded version does not confirm the order.
- Approving a quote keeps `Order.version` unchanged, so a customer can then accept it (end to end: approve, then accept, gives Confirmed).
- A revised version stays hidden until it is approved again.
- If `OrderService.confirm` fails, no acceptance and no confirmation are stored, and a retry then succeeds exactly once.
- A golden test using the seed data: 24 vanilla cupcakes, expected total about 94.23. Confirm the figure during implementation.

## 6. BAKE-34 (stretch)

Deterministic profit by order and by product, calculated from the ledger, so that BAKE-25/26 can use it. The AI explanation can follow later.

## 7. Decisions to confirm with the team

1. **Order status.** Use `Quoted` when the owner approves a quote, matching the merged `ALLOWED_MOVES` (architecture §4.1.1 goes straight from Draft to Confirmed). Record this as a deviation. Agreed in Carl's review of #12, provided the order version is kept (§5). Still to confirm with Angelina (BAKE-8).
2. **`OrderService.confirm`.** No OrderService exists, so Josh adds a minimal method in `backend/app/sales/`, with Ziyi reviewing.
3. **Interfaces needed by Wednesday.** `ApprovalService.request`, `decide` and `is_approved(RecordRef)` from Angelina; `current_owner` and `current_customer` from Ziyi.
4. **`run_ai_task` error handling.** It currently catches every exception, and BAKE-41 will narrow that. The BAKE-22 tests must not depend on the current behaviour.

## 8. Risks

| Risk | Mitigation |
|---|---|
| BAKE-14 or BAKE-15 slips | Build BAKE-23 against the agreed interfaces with test doubles, and merge it after them |
| Migration head conflicts | Rebase onto `main` before merging each migration |
| Student Pack approval is delayed | Keep "checks pass plus one review" as a team rule, and protect `main` as soon as Pro is granted |
