# Jira handoff and initial backlog

This is a planning seed derived from the Stage One report, not an existing Jira backlog or a committed sprint schedule. The supplied report contains no dated sprint plan. No tickets, estimates, assignees or completed sprints have been invented.

Use **BakeFlow AI** as the project name. A possible key is `BAKE`, subject to availability. Jira will be the live source for work status, prioritisation, estimates and sprint commitments. GitHub provides commits and pull requests. A separate GitHub Projects board is not needed for this handoff.

## Backlog seed

All eight core use cases below are proposed **Must-have** items, reflecting the mandatory module scope in Stage One. Split them into small, testable stories during refinement. The final column identifies minimum acceptance topics, not a replacement for the report's complete alternative flows.

| Requirement | Use case / initial story | Acceptance topics |
| --- | --- | --- |
| AR-01 | UC-01: As a customer, I want to submit and review a custom order so that the bakery captures my requirements accurately. | Clarification; owner review for ingredient/allergen exceptions; validated audited draft; no sale confirmation or stock deduction |
| AR-01 | UC-02: As a customer, I want to track, change or cancel my order so that I can manage my request. | Own-order access; permitted state changes; customer confirmation; owner escalation; renewed approvals; stale-state and save failures |
| AR-02 | UC-03: As the owner, I want to check recipe-based requirements so that I can obtain a shortage-only shopping list. | Recipe yields and units; other-order commitments; empty list when sufficient; received stock recorded separately; deterministic result survives explanation failure |
| AR-02 | UC-04: As the owner, I want to approve a feasible production schedule so that confirmed orders can be completed on time. | Confirmed orders; stock, capacity and timing checks; non-overlap; task coverage; current-version approval; guarded save and revision |
| AR-03 | UC-05: As the owner, I want to approve a calculated quotation so that the customer can accept reliable terms. | Deterministic cost and margin; explanation validation; manual-review flags; approval before sending; current-version acceptance before order confirmation |
| AR-03 | UC-06: As the owner, I want to record income and expenses so that I can review order and product profitability. | Invalid/duplicate rejection; valid transaction stored once; deterministic profitability remains available when AI explanation fails |
| AR-04 | UC-07: As the owner, I want a campaign proposal based on business summaries so that promotions reflect stock, profit and capacity. | Validated summaries; seasonal context; low-data notice where permitted; valid proposal; version-specific approval and revision |
| AR-04 | UC-08: As the owner, I want to review promotional content so that only validated and approved material can be exported. | Approved campaign; product/price/availability validation; one current-version approval request; calendar and manual export; edits invalidate approval |

Create supporting work for workflow routing, authentication, shared records, audit history, versioned approval, server-side LLM integration, bounded retries, and production execution under IP-01–IP-05. Production completion and once-only stock deduction are downstream of UC-04 and need explicit acceptance coverage. Also track integration, technology decisions, demo preparation and contribution evidence.

## Dependencies for sprint planning

- Order capture and recipe resolution precede inventory checking and quotation. Owner-approved quotation plus customer acceptance precedes confirmed-order scheduling.
- UC-04 includes UC-03. Received ingredients resolve shortages; creating a shopping list does not change stock.
- Completing all production tasks triggers one atomic deduction for the order.
- Marketing consumes validated business summaries; approved campaign planning precedes content generation and approval.
- Authentication, approval, validation and audit controls belong in each relevant increment.

Choose sprint goals and dates with the team, producing usable increments that can be reviewed. Apply the lab's MoSCoW labels and team estimates; optional work must be identified explicitly. Record the actual start and finish dates rather than backdating activity.

## Story information

For each Jira story record the user goal, AR/UC/IP references, relevant report section or figure, acceptance criteria, dependencies, priority, estimate, owner, validation approach and definition of done. Link the resulting pull request and evidence. Retain stakeholder feedback and follow-up work in Jira.

Suggested definition of done: acceptance criteria demonstrated; applicable model and business rules preserved; relevant checks passed; changes reviewed and integrated; documentation updated; Jira linked to the implementation and its evidence. Planning documents alone do not complete an implementation story.

## GitHub linkage convention

After the private repository exists and the Jira integration is connected, include the real Jira issue key in the branch name, commit subject and pull-request title, for example `feature/BAKE-12-order-draft` and `BAKE-12 Capture validated order drafts`. `BAKE-12` is illustrative and is not an existing ticket.

Configure the connection from the team's Jira/GitHub administration and select the private BakeFlow repository. Record the actual Jira project URL here after setup. Verify the connection with the first real issue and related development change. Connection and credentials are not present in this snapshot.

## Agile evidence

Lab Notes 8–9 and the Agile lecture call for sprint planning, brief daily updates, reviews and retrospectives. Preserve what was completed, what is next, blockers, review feedback and improvements. Keep actual effort/progress history and burndown information. When using AI-assisted coding, record representative failures and independently review generated changes and tests.
