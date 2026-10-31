# BAKE-43: record income and expenses from the Finance page

Implements UC-06 steps 1-4 in the browser. BAKE-24 built the ledger API (`POST /api/finance/transactions`), but the owner had no form for it, so a demo could only show customer payments. The page now records costs such as ingredient invoices, and the profitability figures include them.

**No backend or ledger rules change.** The server still validates every field, refuses a repeated source reference and records each transaction once.

## What is built (`frontend/src/`)

- **`owner/FinancePage.tsx`: "Record a transaction" form**
  - **Fields:** type (Income or Expense), amount (AUD), source (Invoice, Receipt or Voucher), reference, date, and optionally an order.
  - **Order:** the list offers the orders already in the ledger (`byOrder`), such as a paid order that a cake box was bought for. "None (general cost)" is the default. There is no owner order list API yet, so an order that has no transactions can't be picked.
  - **Amount:** must be above zero with at most two decimals, the same rule as the quote page. Otherwise the button stays disabled and nothing is sent. It is sent as a decimal string padded to two places (`50` becomes `"50.00"`), never a number.
  - **Date:** sent as local midnight in ISO format, so today's date is never "in the future".
  - **Invalid input (2a):** the server's message is shown with each field to fix, such as "Date: cannot be in the future". The form keeps what was typed.
  - **Duplicate (2b):** the server's "Invoice INV-1042 is already recorded. Nothing was changed." is shown.
  - **After a save (step 4):** the page says what was recorded, reloads the profitability figures from the ledger and clears any old explanation, which described the previous figures.
- **`api/client.ts`**
  - `recordTransaction(entry, csrfToken)` and the `TransactionInput`, `LedgerEntry` and `FieldIssue` types.
  - `ApiError` now carries `issues`. A 422 that lists fields to fix keeps the server's message and the list. Any other 422 still shows the generic message, as before.

## Verification

`frontend/src/test/FinancePage.test.tsx` (4 new tests):
- an expense is sent with exactly the entered values (amount `"50.00"`, the chosen order, CSRF token), the confirmation is shown and the figures reload;
- a server validation error shows the field to fix, nothing is reported as saved, and the entered amount was sent as `"12.50"`;
- a duplicate reference is reported as already recorded;
- `5.001`, `0`, `-4`, `1e3` and `12,50` are never sent.

The ledger rules themselves are tested in BAKE-24's `tests/unit/test_ledger_validation.py` and `tests/integration/test_ledger.py`.
