# BAKE-22: quote explanation

Implements UC-05 step 4 and flow 4a (AR-03). The Pricing and Bookkeeping Agent explains a quotation that `FinanceService` has already calculated. The system checks that every figure in the explanation matches the calculation. After at most two retries, the quote is flagged for manual review. The AI never changes a financial value.

## Code (`backend/app/pricing_bookkeeping/`)

- **`agent.py`**
  - `PricingBookkeepingAgent.explain_quote` builds one prompt (`PROMPT_VERSION = "quote-explanation-v1"`) and calls the LLM through `ILLMClient`, so the call goes through BAKE-9's gateway or the Mock. It parses the reply into `FinancialExplanation`: `narrative`, `referenced_amounts[{label, value}]` and `warnings`. The model rejects extra keys and has no price fields.
  - `allowed_figures(calc, units)` builds the only figures an explanation may mention:
    - the money figures: material, labour, packaging, additional, subtotal, total and profit;
    - derived values calculated here: price per unit (single-product orders only), labour hours and minutes, the unit count, and the target and realised margin as percentages.
  - `check_explanation` is deterministic. Every referenced amount must equal its labelled figure, and the total must be referenced. Every number in the narrative must be on the allowed list: an amount written with `$` or `AUD` must be a money figure, and a `%` must be a margin. Anything else, such as a self-derived "$24.00 per dozen", an invented fee or a rounded total, is rejected.
  - `QuoteExplainer.explain` runs the agent through `WorkflowService.run_ai_task` with `check_explanation` as the usability test, giving the initial attempt plus two retries (BAKE-8). If no attempt is consistent, it returns no explanation with `requires_manual_review=True` and records `QuoteExplanationFailed`. A quote that the calculation already flagged (`LOW_PROFIT`, `UNUSUALLY_HIGH`) still needs review even when the explanation is good.
- **`fixtures.py`**: a valid Mock reply built from the figures, and a variant with the total off by one cent. These are used by the tests and by BAKE-23's Mock mode.

## Checks added from Carl's review of #19

- **Numbers in words are rejected** (zero to ninety, hundred, thousand, million, dozen, half). The prompt already asks for figures exactly as given, so "about ninety dollars" can no longer slip past the digit check.
- **Light label check:** the first number after "total", "price", "charge" or "quoted" must be the total or the price per unit. The first number after "cost" (including "total cost") must be one of the cost figures. This catches "the total price is $52.80" (the subtotal) and "it costs $75.43 to make" (the total).
- **Why it failed:** `ExplainedQuote.problems` keeps the reasons the last attempt was rejected, so the owner can see why a quote went to manual review.
- **Minus signs are read** (second review). A `-` or `−` before or after the `$` makes the number negative, so "-$48.00", a profit of "-$9.60" or a "-20.00%" margin no longer pass as the real figures. A hyphen right after a letter, digit or dot is not a sign, so "24-cupcake" and "1.5-hour" are unaffected.
- **Known limitation:** the label check only looks at the first number after those words. Other wording can still misstate a figure, for example "the profit is $38.40" when that is the subtotal. The owner always sees the calculated figures next to the explanation, and must approve every quote.

## Decisions (from Carl's review of #12)

- The agent is given its derived figures and told to do no arithmetic. This keeps the checker strict without rejecting natural sentences like "$2.00 each" or "1.5 hours of labour".
- `raw_material_cost` is not offered, so only the rounded material cost can appear.

## Deviation from the Sprint 2 plan

- The plan added a `quotes.explanation` column in this story. Nothing saves quotes until BAKE-23, so the column and its migration move to BAKE-23, where the quote draft and its explanation are saved together (TX-QUOTE-DRAFT). BAKE-22 is the agent, the check and the retry/fallback logic.
- `run_ai_task` currently treats any exception as a failed attempt. BAKE-41 will narrow this. These tests only use `LLMCallFailed`, malformed JSON and schema failures, so they will still pass after that change.

## Verification

`tests/unit/test_quote_explanation.py` (16 tests, Mock LLM, no database):
- **Allowed figures:** the derived values are included, and price per unit appears only for single-product orders.
- **The check rejects:** a wrong referenced amount; an invented amount in the narrative; a self-derived figure; a wrong margin percentage; an unknown label; and a missing total. Hours and unit counts from the list are accepted.
- **Retries:**
  - a consistent reply succeeds on the first call;
  - a wrong amount is retried and then accepted;
  - 3 inconsistent replies end in manual review with an audit entry;
  - timeouts and malformed JSON count as attempts.
- **Figures:** they are identical with or without an explanation, and calculation flags still require review.
