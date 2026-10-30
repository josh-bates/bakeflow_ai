# BAKE-34: profitability by order and product

This PR is the deterministic part of UC-06 step 4 (AR-03). Profitability is recalculated from the recorded ledger, per order, per product and overall. Angelina's marketing business summary (BAKE-25/26) needs these figures, so they come first. The ticket allows the AI explanation to follow later.

## Code

`backend/app/pricing_bookkeeping/profitability.py`:
- `summarise(facts, product_names)` is a pure calculation from ledger facts. It works out income, expenses, profit and margin for the whole ledger, for general (unattributed) transactions, for each order and for each product. Lists are sorted by profit, highest first.
- `ProfitabilityService(store).summary(period_start=, period_end=)` reads `transactions` (and product names) in one unit of work. It takes an optional period, which includes its start and excludes its end and needs a time zone. **BAKE-25 should call this**, so the owner view and the marketing summary use one calculation.

## Rules

- **Attribution follows report §15:** a transaction belongs to at most one order and one product. An order's profit uses the transactions recorded against that order, and a product's profit uses the transactions recorded against that product. Transactions with neither are general, and count only in the overall totals. The per-order and per-product lists are two views of one ledger, so they don't add up to each other.
- **Money is exact.** Stored amounts are in cents, and sums of cents need no rounding. The only rounded value is the margin (profit ÷ income), rounded once to 4 dp with `ROUND_HALF_UP`, the same rule as quotes. With no income, the margin is shown as none instead of being divided by zero.
- **No AI involvement.** The figures are calculated before, and without, any explanation, so an explanation failure (5a) can never hide them.

## Reading the figures (for BAKE-25 and the owner view)

From Carl's review of #20: ingredient invoices are usually general, not recorded against one order, so **per-order margins will look close to 100%**. Show `general` expenses next to the per-order and per-product lists, so nobody reads per-order profit as the bakery's real profit. The marketing business summary (BAKE-25) should use `overall` for profit.

## Part 2: API and owner view (#14, #18 and #20 merged)

- **`GET /api/finance/profitability`** (owner only, `Depends(require_owner)` and BAKE-14's `/api/finance` guard):
  - Optional `start` and `end` give an ISO period with a time zone, including the start and excluding the end. A missing time zone, or an end not after the start, returns 422.
  - Returns `overall`, `general`, `byOrder` and `byProduct`. Each has `income`, `expenses`, `profit` and `transactions`, plus `margin`, which is `null` when there is no income.
  - All money is sent as **decimal strings**, never JSON numbers.
- **Owner "Finance" page** (`frontend/src/owner/FinancePage.tsx`):
  - Shows overall and general figures, then tables by product and by order.
  - Displays the server's amounts exactly as given. The client refuses a response containing a non-string or non-2-dp amount.
  - Shows general costs next to the per-order list, with a note that per-order margins overstate profit, as Carl asked on #20.

## Part 3: AI explanation (UC-06 steps 5-7, 5a)

`backend/app/pricing_bookkeeping/profit_explanation.py`, built on BAKE-22's agent and check (#19):

- **Only on request.** `POST /api/finance/profitability/explanation` (owner only, CSRF) returns the same figures as `GET /api/finance/profitability` plus `explanation` and `notice`. The Finance page has an "Explain these figures" button, so loading the page never waits on a model.
- **Figures only.** `profit_figures` lists what the agent may mention: overall income, expenses, profit and margin, general income and expenses, and the top three products' income and profit, with their names. The prompt (`profit-explanation-v1`) says never to calculate, and says that product profit leaves general costs out.
- **Checked.** `check_profit_explanation` requires `overall_profit` to be referenced. Every referenced amount must equal its figure, and every number in the narrative, with its sign, must be a listed figure; a percentage must be the margin. Numbers written in words are refused. A loss is accepted only with its minus sign.
- **Retry and 5a.** It runs through `WorkflowService.run_ai_task` (three attempts; saved in `ai_requests` once BAKE-37 merges). If nothing passes, the response carries the unchanged figures and "Explanation unavailable. The figures above are unchanged.", and the page keeps showing them.
- **Mock mode** builds a consistent explanation from the figures (`fixtures.valid_profit_explanation`). Live mode uses `LLMGateway`; with no provider configured, every attempt fails and 5a applies.
- Nothing is saved: the explanation describes the figures returned with it.

## Verification

- Part 3: `tests/unit/test_profit_explanation.py` (12 tests: the figures, the Mock reply passing the check, an invented, negated or wrongly labelled number and words refused, a loss with its sign, success, failure after three attempts keeping the figures, provider errors counting as attempts, and the prompt); the route's permissions in `tests/unit/test_profitability_routes.py`; a PostgreSQL API test of a Mock explanation and a failed one with the figures unchanged; and `FinancePage.test.tsx` (no call until asked, the CSRF token, the explanation shown, the notice on failure, the figures kept).

- Part 2: `tests/unit/test_profitability_routes.py` (no session gives 401, a customer gets 403, bad periods give 422); an API test in `tests/integration/test_profitability.py` checks the exact decimal strings and a period filter; `frontend/src/test/FinancePage.test.tsx` checks that the figures render exactly and that float money is refused.

- `tests/unit/test_profitability_calculation.py` (8 tests): a fixed sample ledger, worked by hand, gives the expected overall, per-order and per-product figures and margins. Also covered: general expenses, exact cents (0.10 + 0.20 = 0.30), losses, no income, an empty ledger, and an unknown product.
- `tests/integration/test_profitability.py` (4 tests, PostgreSQL): profit is recalculated from stored `Transaction` rows; a new transaction changes the next summary; period bounds work; a time zone is required.
