# Sprint 1 → Sprint 2 boundary (review, retro, planning)

> No meeting was held on Mon 2026-10-19. On 2026-10-20 Carl closed Sprint 1 in Jira (02:58), started Sprint 2 with the split below (03:09) so work could begin, and opened the review, retro and planning thread in the group chat. This log records that thread.
>
> Replies so far (WhatsApp group; screenshot: [img/2026-10-21-sprint1-retro-replies.png](img/2026-10-21-sprint1-retro-replies.png)): Josh on 20 Oct (edited 20:08) and Ziyi on 21 Oct (19:26). The dates come from the chat's date divider. Both give retro points, and neither asks for a change to the Sprint 2 split. **Angelina has not replied yet. Her points will be added when she does.**
>
> Written by Carl on 2026-10-22 from Jira, GitHub and the chat replies.

---

# Sprint Review — Sprint 1

- **Date(s):** chat thread 2026-10-20 → 2026-10-21 (open for Angelina)
- **Where:** Group chat (no Monday meeting)
- **Participants:** Carl (facilitator), Josh (replied 20 Oct), Ziyi (replied 21 Oct); Angelina: no reply yet; tutor: none
- **Sprint goal:** Repo, tech stack and data model set up; WorkflowService skeleton with the AI retry budget; LLM gateway with mock; Sales, Inventory and Pricing each deliver one tested deterministic rule (Marketing module interface stubbed only); thin end-to-end slice: submit custom order → draft saved.
- **Goal met?** Mostly. Every listed part is merged. The end-to-end slice (custom order → validated draft → shopping list, 600 g flour, no shortage) works through the API. It does not work in the browser yet, because customers cannot sign in until BAKE-14.

## 1. Completed (meets DoD)

| Jira key | Story | Demoed? | Evidence (PR / commit / screenshot) |
|---|---|---|---|
| BAKE-6 | Repo, tech stack, project skeleton (Carl) | No live demo | PR #2 (architecture), PR #6. PR #11 (built UI in the Docker image) still open |
| BAKE-7 | Shared data model + ValidatedRecordStore (Carl) | No live demo | PR #8 |
| BAKE-8 | WorkflowService routing and audit (Angelina) | No live demo | PR #4. Review comments open as follow-ups |
| BAKE-9 | LLMGateway with mock client (Angelina) | No live demo | PR #5. Review comments open as follow-ups |
| BAKE-10 | Browser UI shell (Ziyi) | No live demo | PR #7 (merged through #9) |
| BAKE-11 | Custom order → validated draft, UC-01 (Ziyi) | No live demo | PR #9 |
| BAKE-12 | Ingredient needs + shortage-only list, UC-03 (Carl) | No live demo | PR #10. End to end with BAKE-11: 600 g flour, no shortage |
| BAKE-13 | Deterministic quotation, UC-05 1–3 (Josh) | No live demo | PR #3, merged without a review to meet the deadline (Josh, retro reply) |

## 2. Not completed

| Jira key | Status | Reason | Decision (carry over / split / defer — who agreed) |
|---|---|---|---|
| — | — | All 8 stories done | — |

Done, but with follow-up work:
- BAKE-8/9: review findings left as follow-ups when PRs #4/#5 were merged on 2026-10-19 (Carl decided to merge first).
  - BAKE-8: `run_ai_task` swallows every exception; Scheduled orders cannot be cancelled; should use the store and the shared `RecordRef`.
  - BAKE-9: timeouts, resets, incomplete reads and non-UTF-8 replies escape as non-`LLMCallFailed` errors; enforce https.
- BAKE-6: its acceptance criterion "built React shell" is met only once PR #11 is merged.
- BAKE-10: `docs/bake-10-interface.md` asks for screenshots attached to Jira before Done.
- BAKE-13: PR #3 had no review before merging.

## 3. Demo notes

- No live demo was held. Ziyi's reply confirms the frontend and order submission flow worked together (summary confirmation, draft saving, audit records, passing tests).

## 4. Stakeholder / tutor feedback → backlog changes

| Feedback | From | Backlog change made | Jira key(s) |
|---|---|---|---|
| BAKE-8/9 review findings | Carl (PR review) | New 2-pt bug for Angelina in Sprint 2 | BAKE-41 |
| No tutor feedback this sprint (no lab meeting) | — | — | — |

## 5. Metrics

- Committed: 36 pts · Completed: 36 pts · Carried over: 0 pts
- Timing:
  - The burndown stayed flat from 13 to 18 Oct.
  - Merged before the planned end (19 Oct 17:00), 13 pts: BAKE-13 (18 Oct 20:57), BAKE-6 (19 Oct 00:17), BAKE-7 (19 Oct 01:15).
  - Merged after the planned end, 23 pts: BAKE-8/9 (19 Oct 23:28), BAKE-10/11 (19 Oct 23:41), BAKE-12 (20 Oct 00:09).
  - Jira shows no scope changes. One 5-pt story was briefly reopened on 20 Oct before closing again.
  - Sprint closed in Jira at 20 Oct 02:58.
- Burndown: [img/2026-10-20-sprint1-burndown.jpg](img/2026-10-20-sprint1-burndown.jpg). Board before closing: [img/2026-10-20-sprint1-board-before-close.jpg](img/2026-10-20-sprint1-board-before-close.jpg)

---

# Retrospective — Sprint 1

- **Date(s):** chat thread 2026-10-20 → 2026-10-21 (open for Angelina)
- **Where:** Group chat
- **Participants:** Josh (written reply, 20 Oct), Ziyi (written reply, 21 Oct), Carl (facilitator); Angelina: no reply yet
- **Facilitator:** Carl

## 1. Follow-up on last retro's actions

| Action from Sprint 0 | Owner | Done? | Effect |
|---|---|---|---|
| — (first sprint) | | | |

## 2. What went well

- **Josh:** "BAKE-13 merged on time with 30 tests, and the quote formula is document[ed]."
- **Ziyi:** "The frontend and order submission flow worked together, including summary confirmation, draft saving and audit records, and the automated tests passed."
- **Carl:**
  - The stack and shared foundation were in place before module work.
  - Every story landed through a PR with tests, and CI runs on PRs.
  - Reviews found real bugs, and each fix came with a regression test.

## 3. What did not go well

- **Josh:** "I started late, so all my progress landed on 18 Oct, and I merged PR #3 without a review to make the deadline."
- **Ziyi:** "Several UI revisions, unclear interface details and dependencies on unmerged PRs led to extra rework and integration effort."
- **Carl:**
  - Coding started around 17 Oct, so the burndown was flat for five days and 23 pts landed after the deadline.
  - Stacked PRs (#9 contained #4, #5 and #7) caused conflicts and rushed reviews.
  - Jira cards were not moved during the sprint.
  - No review or retro meeting was held on 19 Oct.

## 4. Root causes (for the top 1–2 problems)

- **Work started late, so everything landed at the end.** Implementation only began once the stack and architecture were settled (18 Oct). Josh's and Carl's work, and most merges, fell into the last two days. This also led to merging without review (PR #3) and merging with known issues (PRs #4/#5).
- **Interfaces were settled while coding, not before.** Unclear interface details and building on unmerged PRs caused rework (Ziyi). Josh also asks to agree shared interfaces early.

## 5. Improvement actions (max 3, concrete and checkable)

| Action | Owner | How we will check it next sprint |
|---|---|---|
| Agree shared interfaces before coding a dependent story (e.g. ApprovalService, `current_owner`/`current_customer`, the FinanceService calls for BAKE-22/23). Proposed by Josh and Ziyi | Everyone | Interfaces written down in a plan or PR before the dependent story starts. Josh's plan (PR #12, §7) already lists his |
| Small PRs with a named reviewer, no PR built on an unmerged PR, test regularly. Proposed by Josh, Ziyi and Carl | Everyone | Every Sprint 2 PR has a requested reviewer. Already true for #12 and #13 (20 Oct), reviewed by Carl on 21 Oct |
| Post a short async stand-up and move your Jira card whenever you make progress; mid-sprint check on Thu 22 Oct. Proposed by Carl | Everyone; Carl runs the check | Burndown never flat for more than 2 days; BAKE-14 and BAKE-15 merged by Wed 21 Oct night |

---

# Sprint Planning — Sprint 2

- **Date(s):** chat thread 2026-10-20 → 2026-10-21 (open for Angelina)
- **Where:** Group chat
- **Participants:** Carl (facilitator), Josh (replied 20 Oct), Ziyi (replied 21 Oct); Angelina: no reply yet
- **Facilitator / note-taker:** Carl
- **Sprint dates:** 2026-10-20 03:09 → 2026-10-26 17:00 (Stage 1 interview at 17:00 that day)
- **Jira sprint:** "Sprint 2 - Confirmed order" (id 4, board 2), started 2026-10-20 03:09 by Carl

## 1. Inputs

- **Previous sprint:** committed 36 pts, completed 36 pts (velocity 36, but 23 pts landed after the deadline)
- **Carried over:** none
- **Feedback to act on:** BAKE-8/9 review findings; PR #11 for BAKE-6; the Sprint 1 retro actions above
- **Team availability this sprint:** not discussed in the thread

## 2. Sprint goal

> In the browser, a signed-in customer's validated draft gets a deterministic quote; the owner approves it and the customer accepts → Order Confirmed. Start cross-order stock + schedule proposal and the ledger; first deployment from a clean zip.

## 3. Committed backlog (in Jira)

| Jira key | Story (short) | Points | Owner | MoSCoW | Depends on / notes |
|---|---|---|---|---|---|
| BAKE-14 | Auth and access control | 3 | Ziyi | Must | Do first: blocks 18, 23 and all owner endpoints. Target Wed 21 Oct |
| BAKE-16 | Clarification questions (UC-01 3a/3b) | 3 | Ziyi | Must | 9, 11 done |
| BAKE-18 | Order tracking (UC-02) | 3 | Ziyi | Must | After 14 |
| BAKE-15 | ApprovalService | 3 | Angelina | Must | Do first: blocks 19, 21, 23, 17, 30. Target Wed 21 Oct |
| BAKE-25 | Business summary (UC-07 2) | 3 | Angelina | Must | Fixtures until 24/34 land |
| BAKE-41 | BAKE-8/9 review fixes (bug) | 2 | Angelina | Must | Findings from PR #4/#5 |
| BAKE-22 | Quote explanation (UC-05 4) | 3 | Josh | Must | 9, 13 done |
| BAKE-23 | Quote approval + customer acceptance | 5 | Josh | Must | After 14, 15, 22 |
| BAKE-24 | Ledger without duplicates (UC-06) | 3 | Josh | Must | 14 for owner-only access |
| BAKE-27 | CI on every PR | 2 | Josh | Should | PR #13: lint + checks on every PR; approved by Carl 21 Oct |
| BAKE-19 | Stock committed to other orders (IP-02) | 3 | Carl | Must | After 15 |
| BAKE-20 | Schedule proposal + validator (UC-04 1–5) | 8 | Carl | Must | Confirmed-order fixtures until 23 lands |
| BAKE-29 | One-command deploy from the Canvas zip | 3 | Carl | Must | Moved from Josh. PR #11 first |

**Total committed:** 44 pts (velocity 36).
**Stretch, only after the owner's core stories are merged:** BAKE-17 Ziyi (5), BAKE-26 Angelina (5), BAKE-34 Josh (3), BAKE-31 Carl (2) = 15 pts. Assigned in Jira but left in the backlog, not in the sprint.

## 4. Risks and Definition-of-Done reminders

- Common DoD: reviewed PR with the Jira key in the title, automated tests pass, no secrets committed.
- Risks:
  - 44 pts is above last sprint's 36; the Thursday check decides whether stretch work starts.
  - If only the committed 44 pts land, Sprint 3 still has 57 pts (BAKE-17, 21, 26, 28, 30–40). Cut order from architecture §11.2 applies: BAKE-28 first, then simplify the optional features.
  - BAKE-14/15 slipping blocks most of the sprint.

## 5. Decisions

- Carl started Sprint 2 before the replies so the week is not lost; changes from the thread will be applied in Jira and noted here.
- BAKE-29 moved from Josh to Carl, announced in the group message. No objection in the replies so far.
- BAKE-41 created for the BAKE-8/9 review findings.
- Ziyi will prioritise authentication (BAKE-14), as planned. From her retro reply.
- Josh posted his Sprint 2 plan for review (PR #12). Carl's review comments, 21 Oct: the Quoted status must not bump the order version; how to handle derived amounts in AI explanations; a 409 for a reused request key with different content.

## 6. Action items

| Action | Owner | Due | Jira key |
|---|---|---|---|
| Reply with OK / changes and one retro line | Everyone | Wed 21 Oct 12:00 | — (Josh done 20 Oct, Ziyi done 21 Oct, Angelina pending) |
| Rename and start Sprint 2, set assignees, create the review-fix task | Carl | Done 2026-10-20 03:09 | BAKE-14…29, BAKE-41 |
| Apply any changes from the replies in Jira | Carl | Wed 21 Oct | — (none requested so far) |
| Review PR #11 | Ziyi | Wed 21 Oct | BAKE-6 |
| Review PRs #12 and #13 | Carl | Done 2026-10-21 | BAKE-22–24, BAKE-27 |
| Mid-sprint check | Carl | Thu 22 Oct | — |
