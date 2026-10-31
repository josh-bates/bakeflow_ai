# BAKE-17 — Owner exception review

UC-01 3c, UC-02 4c and IP-03. Ingredient-changing requests and uncertain allergen disclosures are saved as pending owner reviews, not as orders. The server does not run inventory checking or pricing until a valid intake resolution has been approved by the owner and confirmed by its customer.

## Browser flow

1. Sign in as the seeded customer. In Mock mode, submit either exact example:
   - `24 vanilla birthday cupcakes for collection on 2 Oct before 12:00, replace milk with oat milk, no allergy concerns`
   - `24 vanilla birthday cupcakes for collection on 2 Oct before 12:00, I am not sure about nut allergies`
2. The response has no draft-confirmation token. The request appears under **Owner reviews**. Ordinary missing disclosure remains incomplete; it is not interpreted as “no allergies”.
3. Sign in as the owner and open **Pending approvals**. Review the original text and conversation. Select an existing product/recipe for every item, check its displayed ingredients, complete the quantities/collection/disclosure, and explain the agreed handling. If no recipe is suitable, ask the customer for clarification or a suitable alternative; do not approve it. Text such as “replace milk” never edits the shared recipe.
4. **Ask for clarification** records a question; the customer replies in Owner reviews. Replies and corrections invalidate the previous resolution and require a fresh owner decision.
5. **Send resolution to customer** validates and records an approval against the exact referral version using `ApprovalService`.
6. The customer refreshes Owner reviews, reads the selected products and all resolved details, and explicitly confirms them. The server checks the current referral approval, future collection time, quantities, product/recipe relation and recipe versions again. Only then does it save a Draft order with `resolved_recipe_id`, allergen disclosures and audit entries in one transaction. The normal stock check runs after that transaction. This remains a draft, not a confirmed sale.

For a convenient offline demo, ask for clarification and agree a standard catalogue alternative where appropriate. The seed does not include an oat-milk recipe; selecting a standard recipe does not make it oat-milk or allergen-safe. Allergen suitability is a human decision, never an AI or keyword guarantee.

## Existing-order exceptions

The customer can open **Request a refund or a change to work in production**, enter their order reference, and describe the request. Refund and InProductionChange reviews use the same Pending → NeedsClarification → Pending → AwaitingCustomer → Completed flow. A production-change request requires the order to be InProduction. Ownership is checked before exposing any order information.

The owner records proposed handling, and the customer can acknowledge or request corrections. This completes the *review only*: it neither pays a refund nor alters the accepted order, released plan, task progress or inventory. Execution belongs to BAKE-30 and the relevant guarded order/payment services. Source order version and status must still match at resolution and acknowledgement; otherwise a new review is required. This prevents an old proposal being applied to work that has advanced in the meantime.

## Implementation and integration

- New `owner_referrals` table: independent versioned queue for Intake, Refund and InProductionChange; original customer text, bounded conversation and owner resolution are retained and audited.
- Design refinement of architecture R9: one shared owner-review table is used for both provisional intake and existing-order exceptions instead of two separate tables. Order lifecycle values are unchanged. An Intake becomes an Order only after customer confirmation.
- `ReferralIntake` adds a small preview hook without rewriting BAKE-11/16 validation. The branch starts from main, not the unmerged BAKE-16 branch. Its preview signature forwards BAKE-16 clarification arguments when that story lands.
- Explicit raw-text signals provide conservative fallback routing when extraction misses an ingredient/allergen concern. They are English routing hints, not a complete language parser or medical checker. Unsupported Mock inputs still fail explicitly.
- Owner operations require authenticated owner identity plus CSRF. Customer listing and actions are scoped to their account. Owner selection uses catalogue IDs and the current recipe version, never a model-generated recipe.
- Repeated identical submissions use a customer-scoped fingerprint under the customer lock. Repeated confirmations lock the referral and return the same saved order; concurrent confirmations cannot create multiple drafts.
- The UI shows loading/errors and retains the proposal after a save error. It makes no recipe, inventory, pricing or refund calculations.
- Migration `bake17_referrals` follows `bake33_deductions`, the current main head at integration. Recheck the parent if another migration lands before merge; do not introduce two heads.

## Validation

`tests/integration/test_owner_referrals.py` covers pending-without-draft, missing disclosure, resolution revalidation, changed recipes, unsupported products, invalid quantities/time, customer corrections, concurrent retries, rollback, existing-order review without side effects, authenticated HTTP flows and other-customer denial.

`frontend/src/test/OwnerReviews.test.tsx` covers explicit customer confirmation, visible resolved details, save failure, corrections returning to owner review, catalogue selection and truthful refund wording. Existing page-navigation tests include the new owner review dashboard.

Verified against main `be6488c`: 585 backend tests and 42 frontend tests pass; Ruff, TypeScript and the production frontend build pass. A disposable PostgreSQL browser rehearsal completed the exact Mock ingredient-change request → owner question → customer accepts a standard alternative → owner selects the catalogue recipe → customer confirms → one Draft appears in Your orders. No live LLM or real payment provider was used.
