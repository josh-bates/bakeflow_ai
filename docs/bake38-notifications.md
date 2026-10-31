# BAKE-38: customer order notifications

Contribution: Zoe — customer order notifications, an optional AR-01 extension added in Stage 2. This feature was not in the Stage 1 feature diagram. It stays within the order-management scope and provides the individual optional feature alongside the customer core use cases.

## Behaviour

The customer page shows an **Order updates** list, refreshed every 15 seconds, when the browser regains focus and after saving a draft. Only the authenticated ordering customer can read it. These are dated historical updates: the order-tracking view remains the source for the current status.

Notifications cover Draft, Quoted, Confirmed, Scheduled, InProduction, Completed and Cancelled. Completed means production is recorded as complete; it does not invent a payment, collection or refund outcome. There is no distinct ReadyForCollection stage in the shared model.

Messages always use the approved deterministic stage descriptions. AI wording is optional in the story and is deliberately omitted for the deadline and clean-zip demo (architecture §11.2 template-first simplification). No LLM request, email service, credentials or new dependency is needed. Provider failure cannot prevent the template from being displayed. GET never invokes AI or writes data.

## Recorded transitions and failure isolation

`ValidatedRecordStore.commit()` passes saved Order changes to `WorkflowService.record_saved_order()`. This records a durable transition and links the order audit to it in the same transaction. Draft creation is included; unchanged status and content-only updates are excluded. Calling `move_order()` in memory, interpreting an AI response, or rolling back a transaction creates no visible update. This integration covers quotation approval/acceptance and production services without changing each route.

Once the business transaction commits, delivery inserts the rendered notification in a separate transaction. An insertion/rendering failure is caught at this boundary and cannot roll back the successful order operation. The committed transition itself is the durable notification source: the read query joins it to the optional rendered copy and derives the same template when the copy is missing. This covers a crash between business commit and delivery without relying on an in-memory queue or a running worker.

The transition ID is the stable public notification ID. `UNIQUE(transition_id, recipient_id)` plus PostgreSQL conflict handling makes replay/concurrent delivery idempotent. Existing workflow operations already return their saved result on retries; an unchanged status produces no extra transition. A real second visit to Scheduled is a new event even if the order content version is unchanged. Notifications are append-only. No historical notifications are backfilled for pre-migration orders.

Design difference from the general architecture schema: the transition table is scoped to Order events and stores `order_id`/`order_version` instead of a generic subject type/id. Events use the existing order audit's UUID as `event_id`. As agreed for the deadline, this does not introduce the broad `handle`/`dispatch` refactor. Read/unread marking and optional AI polish are not included.

## Migration and verification

Migration `bake38_notifications` follows `bake33_deductions`, the main head used for this branch. If another migration merges first, rebase the parent revision to that new head before merging; run `alembic heads` and `alembic upgrade head` on a disposable database.

Automated coverage includes real PostgreSQL commits and rollbacks, concurrent/repeated delivery, revisiting a stage, delivery failure and recovery, unavailable AI, quotation approval and acceptance retries, cookie-authenticated customer isolation, and frontend automatic refresh/error handling.

Manual demo:

1. Sign in as the seeded customer, submit a supported request with collection time and explicit allergen information, and confirm the summary. The Draft update appears.
2. As owner, prepare and approve the quotation for that order using the quotation UI when its PR is merged. Drafting alone must not create a Quoted update.
3. Return to the customer's page (or leave it open in another browser session). Within 15 seconds, the Quoted update appears once.
4. Accept the quotation. Confirmed appears once; repeating the action must not add another copy.
5. Sign in as another customer: none of the first customer's notifications appear.

The current main branch has quotation services but not its approval UI. The real approval/acceptance path is exercised by the integration test; the browser owner-approval step also needs that UI PR merged.

Verification on this branch: 590 backend tests passed using a disposable PostgreSQL database (including migration/schema checks); 42 frontend tests passed; TypeScript, frontend production build and backend lint passed. The local browser demo verified sign-in, summary confirmation, draft persistence and automatic display of the Draft notification. Owner quotation approval/acceptance was verified through the real services in the PostgreSQL integration test; the browser approval UI is not on this branch's main baseline yet.
