# BAKE-30 — customer order changes and cancellation

Zoe's UC-02 / AR-01 contribution: customers preview a permitted amendment or cancellation and explicitly confirm it. Nothing is saved by interpretation or preview. Discarding the preview makes no request and changes no order. The customer page also shows confirmed request history and owner replies; the owner's Orders page has the review queue.

## Supported prototype behaviour

- **Draft/Quoted:** a standard single-item quantity change or explicit collection date/time is revalidated and saved only after customer consent. A fresh stock check is saved atomically, old quotation approvals are invalidated, and a fresh quotation revision/approval is required. A Quoted order stays Quoted but the invalidated quotation cannot be accepted.
- **Confirmed/Scheduled:** normal quantity/time amendments stay pending. The owner prepares a revised quotation, inspects its calculated price, then approves that exact version. Only the customer's subsequent acceptance updates the original order and its accepted-quote reference together. Until then, the original accepted terms remain authoritative. Existing additional costs are retained when repricing. Revised stock requirements are checked and saved, and affected plans need renewed review/release.
- **Cancellation before work/payment:** a confirmed cancellation moves the order to Cancelled. Future commitments for that cancelled order cease under the existing stock/capacity rules. It never writes a refund, changes recorded on-hand stock or deletes production evidence.
- **Refunds, any started/completed task, deductions, open/succeeded payments, and recipe/product/allergy exceptions:** go to the owner review queue without applying the requested amendment or cancellation. Owner replies/declines are audited. Automated refund execution and modification of started work are intentionally excluded; these require the separate owner resolution process (UC-02 4c / BAKE-17). A completed/cancelled order cannot be changed or cancelled again, but a refund review may be requested.
- **Pending review:** affected production planning/release/progress and new payment initiation are blocked. Released-plan resource snapshots are retained, so paused work does not silently release committed stock. Withdrawal or rejection preserves original terms; abandoned amendment quotes cannot prevent payment of the original approved contract. A changed order version prevents old plan tasks from progressing even after its amendment is applied.

The simple-change interpreter supports one operation at a time. Mock examples are `change quantity to 36`, `make it 36`, `collection at 2027-10-02T12:00:00+10:00`, and `collection before 2027-10-02T12:00:00+10:00`. Explicit at/before wording is retained; otherwise the existing collection kind stays unchanged. Complex requests are reviewed rather than partially interpreted. Ambiguity, unsupported structure or invented quantity/date evidence consumes at most the shared initial attempt plus two retries, then returns an explanation and leaves everything unchanged. Model calls go through `WorkflowService.run_ai_task`; no AI response executes a change.

## Transactions and permissions

Preview returns a 30-minute signed token scoped to this operation, authenticated customer, order, proposed terms, original state and expected outcome. It contains no credentials. Confirmation rechecks the current order, latest order audit (including same-version stage changes), quotes/decisions, tasks and payments after taking the shared constraint guard and order row lock. Requests that moved on are rejected. Replaying an already confirmed token returns the same request; an unchanged replay makes no writes.

Order edits, stock rechecks, approval invalidation and the request/audit are one transaction. Revised quotation acceptance and the revised order save are also one transaction. Errors produce no success response or partial update. Customer reads/writes use the authenticated ordering identity; owner actions require the owner role. Every write endpoint checks CSRF. Owner review and customer acceptance use request versions to reject stale decisions.

`order_change_requests` implements architecture [R9]. The request state names refine the design to Pending, AwaitingApproval, Quoted, Applied, Rejected and Withdrawn so the browser distinguishes preparation, owner approval and customer acceptance. The new table is an in-place versioned record saved through the shared store. `Quote.pending_change_id` links the revised quote; `source_order_version` identifies the future order version that becomes real only on acceptance. Original quotation versions and payment/production records are retained.

## Integration notes

This branch starts from main, not the unmerged BAKE-17 or BAKE-38 branches. It does not duplicate the intake referral table or change BAKE-17's recipe resolution. Its order-change queue can appear alongside BAKE-17's intake/exception queue when those pages merge. The broad `handle`/`dispatch` refactor remains out of scope. Existing class names and shared application styles are preserved.

Migration `bake30_order_changes` currently follows `bake33_deductions`. If BAKE-17, BAKE-38 or another schema migration merges first, update the parent revision before merging and verify one Alembic head. This PR changes small guard checks in quotation, payment and production services, so those modules' tests are included in regression verification.

## Demo and tests

1. Sign in as customer. Choose an existing Draft/Quoted order under **Adjust an order** and preview `change quantity to 36`. No request is listed yet. Withdraw the preview: the order is unchanged.
2. Preview again and confirm. The order quantity changes, its version advances and the previous quote is unavailable until repriced/approved.
3. For a Confirmed order, preview and confirm a change. It stays Pending with the original quantity. As owner, open **Orders**, enter a review note and prepare the revised quotation. Check the price, then approve it. As customer, refresh requests and accept the displayed revised quotation: only now do the terms change.
4. Preview cancellation, then let the owner move the order to a later stage. Confirming the old preview must say the request is stale.
5. Refund and in-production requests appear for owner review and do not execute refunds or reverse deductions.

Automated coverage exercises preview/withdrawal, same-version stale states, concurrent confirmations, cancellation versus release, quote invalidation, amendment approval/acceptance, original-contract payment after withdrawal, customer isolation, CSRF, AI retry exhaustion and simulated save failures at both application steps. Frontend tests require explicit confirmation, retain uncertain-save tokens for retry, require fresh previews after conflicts, and show prices before customer/owner acceptance.

## Validation results

- Backend: 604 tests passed against a disposable PostgreSQL database; Ruff passed.
- Frontend: all 44 tests passed across the full run and the affected-suite rerun; TypeScript checking and the production build passed.
- Alembic upgrade and metadata comparison passed.
- Browser rehearsal: a customer requested a change from 24 to 36 vanilla cupcakes on a confirmed order. The original quantity remained 24 through request submission, owner quotation preparation, and owner approval. The customer then accepted the revised AUD 141.34 quotation; the order updated to 36 and the request became Applied.
- Review and merge are still required before this story meets the common Definition of Done.

## Integration review

Migration order: BAKE-37 → BAKE-17 → BAKE-38 → BAKE-30. Merge PRs #45, #46 and #47 in that order.

Plan release remains atomic for the exact approved plan version. A pending amendment blocks release of a plan containing that order, but does not block unrelated plans. Silently dropping a task would release a different plan from the one the owner approved. Withdraw/resolve the request, or create and approve a replacement plan before release. Existing ingredient consumption is never reversed.

BAKE-17 retains its exception proposal/acknowledgement queue; BAKE-30 handles commercial amendments and cancellation. Neither refund review route executes a refund.
