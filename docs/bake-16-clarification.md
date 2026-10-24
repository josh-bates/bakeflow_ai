# BAKE-16 — clarify before saving (UC-01 3a/3b, AR-01)

Customers can answer follow-up questions beneath an incomplete summary. Each
answer re-runs extraction against the original request and the ordered previous
answers, followed by the same deterministic validation used for the initial
request. A complete summary still requires explicit confirmation before the
existing audited draft save runs.

`POST /api/order-requests` accepts the original `message` and optional
`clarifications` (up to eight answers, each up to 1,000 characters). The response
adds `questions`; existing `issues` and `confirmationToken` keep their meanings.
The client retains answers only for the current request. Editing the original
request starts a new conversation. Failed extraction retains the unsent answer
for retry, without appending it twice. At the answer limit customers can rewrite
the request or end it. No additional database table or migration is needed.

The extraction schema adds `unclear_fields`. The agent is instructed to flag
ambiguous/conflicting details instead of choosing, and to preserve earlier
facts except where explicitly corrected. Unclear fields block confirmation.
Missing allergen information remains null; an empty list requires an explicit
customer disclosure verified against the original request or answers. The UI
never offers a default 'no allergies' value. Actual allergen concerns and recipe
changes still require owner review; BAKE-16 does not resolve recipes or bypass
that existing restriction.

Unsupported products receive an explanation and the actual catalogue options.
Unsupported recipe changes receive an owner-review explanation. Customers can
revise the request or use **End request without saving**, which discards the
incomplete conversation locally and makes no save request. Only the existing
confirmation endpoint creates a draft and audit entry.

## Offline demonstration

Use the normal seeded-customer sign-in and Mock mode:

1. Submit `24 vanilla birthday cupcakes for collection on 2 Oct before 12:00`.
2. The summary has missing allergen information and confirmation is disabled.
3. Answer `not sure`: it remains incomplete.
4. Answer `no allergy concerns`: review the complete summary, then confirm.

Another example: submit `24 croissants for collection on 2 Oct before 12:00, no allergy concerns`.
Read the unsupported-product explanation. Either end without saving, or answer
`24 vanilla birthday cupcakes instead` and review the new summary.

For the missing-time example `24 vanilla birthday cupcakes, no allergy concerns`,
answer `collection on 2 Oct before 12:00`.

These are exact canned fixtures, not a natural-language parser. Other Mock inputs
fail explicitly; arbitrary natural-language clarification uses the configured
live LLM adapter, which has not been exercised with a real provider in this change.
Each extraction retains the existing maximum of two retries after its first attempt.

## Design refinement and tests

Conversation context is bounded and transient, rather than persisted before
customer confirmation. This extends the Stage 1 intake flow without changing
order state transitions, owner approvals or shared record writes.
Tests cover absent allergen disclosure, unresolved ambiguity, unsupported
products, retained allergy concerns, re-extraction context, no draft before
confirmation, authenticated multi-turn HTTP requests, answer limits and UI exit.
