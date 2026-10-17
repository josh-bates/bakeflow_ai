# Stage One design baseline

Source: [Stage One report](reference/stage-1-report.pdf), dated 12 October 2026. Section references below refer to that report. This summary supports implementation planning; the original report and its diagrams remain the detailed baseline.

## Structure and responsibilities

Sections 3, 7, 8, 13 and 14 define browser-based customer and owner interfaces, a deterministic `WorkflowService`, four specialist agents, deterministic business services, shared approval and persistence services, and a server-side `LLMGateway` implementing `ILLMClient`.

The agents are `SalesAgent`, `InventoryProductionAgent`, `PricingBookkeepingAgent` and `MarketingAgent`. Their common task contract is `AIAgent.execute(TaskRequest): TaskResult`. Agents interpret, explain, plan and propose; they cannot directly commit business changes or grant approvals.

`OrderService`, `InventoryService`, `ProductionService`, `FinanceService` and `MarketingService` perform business validation and calculations. `ApprovalService` records version-specific owner decisions. `ValidatedRecordStore` provides authorised persistence and audit recording. Access control is a capability of `UserAccount` and the store, not an additional class required by the model.

The modules run in one application runtime with a local shared SQL database. Workflow progression uses explicit rules and direct calls. The design does not require independent microservices or an event bus. Browser clients and the external LLM have no direct database connection.

## Business invariants

1. A customer-confirmed order summary creates a validated draft, not a confirmed sale. Confirm the order only after customer acceptance of the current owner-approved quotation.
2. Resolve ingredient-changing requests and uncertain allergen information through the owner before inventory or costing. Use stored recipe information; free text cannot silently change shared recipes.
3. Calculate prices, ingredient quantities and profitability deterministically. Use decimal monetary arithmetic to two decimal places and owner-supplied labour rates and target profit margins.
4. Validate AI results structurally and against business rules. Allow at most two retries after the initial failed or invalid attempt. Apply each use case's failure behaviour: valid inventory or ledger results can remain available without an AI explanation; invalid proposals cannot authorise changes.
5. Bind approvals to the current record version. Revised quotations, schedules, campaign plans and marketing content need renewed validation and approval. Preserve historical decisions.
6. Authorise protected operations and customer access to their own orders. Record business changes, approvals and relevant transitions with audit information. Failed persistence must not be reported as success.
7. Reject duplicate financial transactions. Record an order's ingredient deduction once, atomically with its durable completion/deduction record, after all required production tasks finish.
8. Check stock after accounting for other active approved orders whose ingredients have not been deducted. Count each order once, replace revised schedule versions, and recheck stock and capacity during guarded approval saves.
9. Production plans must contain tasks, cover the candidate confirmed orders, fit daily work-time capacity, avoid overlapping work for the single team and meet required completion times. Production requires current-version schedule approval.
10. Changing planned tasks, times or orders revises the schedule and invalidates approval. Routine task-progress changes do not. Task states are `Planned`, `InProgress`, `Blocked` and `Completed`.
11. Approve a campaign before generating dependent marketing content. Validate referenced products, prices and availability. Require current-version content approval before calendar placement and manual export, including after edits to already scheduled content.
12. UC-07 permits a valid general campaign suggestion with a low-confidence notice when business data is limited. It still requires applicable business checks and explicit owner approval.

## Model traceability

| Report sections | Implementation planning use |
| --- | --- |
| 1–4 | Purpose, scope, AR-01–AR-04, IP-01–IP-05, mandatory and optional features |
| 5–6 | Actors, UC-01–UC-08, success paths, alternatives and failure postconditions |
| 7–9 | Architectural rationale, class relationships, task contracts and runtime example |
| 10–12 | Activities, interactions, approval lifecycles and revision/failure paths |
| 13–15 | Package structure, deployment boundaries and prototype assumptions |
| 16 | Stage One responsibilities and contributions |

The report's example of 24 cupcakes uses a recipe yielding 12 units with 300 g flour per batch: the order requires 600 g. Starting with 1,000 g, stock stays unchanged through schedule approval, then becomes 400 g once both production tasks finish. Reprocessing completion must not deduct another 600 g. These are illustrative values, not production data.

## Decisions left open

Select the implementation framework, database engine and LLM provider during planning. Record changes to the baseline with their rationale and affected requirements. Do not treat optional real payments or automatic publication as committed scope. Resolve the per-member optional-feature requirement noted in [Stage Two planning](stage-two.md) before assigning those stories.
