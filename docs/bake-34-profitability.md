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

## Still to do on BAKE-34

These follow once the PRs they build on are merged, so no PR is stacked:
- **API:** owner-only `GET /api/finance/profitability`. Needs #18 (ledger routes) and #14 (sign-in).
- **AI explanation (UC-06 step 5, 5a):** reuses the BAKE-22 check and retry pattern (#19). If the explanation fails, the figures stay visible.
- **Owner dashboard view.**

## Verification

- `tests/unit/test_profitability_calculation.py` (8 tests): a fixed sample ledger, worked by hand, gives the expected overall, per-order and per-product figures and margins. Also covered: general expenses, exact cents (0.10 + 0.20 = 0.30), losses, no income, an empty ledger, and an unknown product.
- `tests/integration/test_profitability.py` (4 tests, PostgreSQL): profit is recalculated from stored `Transaction` rows; a new transaction changes the next summary; period bounds work; a time zone is required.
