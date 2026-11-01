# Sprint 2 → Sprint 3 boundary (review, retro, planning)

> No meeting was held on Mon 2026-10-26 (the day of the Stage 1 interview). On 2026-10-27 Carl closed Sprint 2 in Jira (02:47), moved its five unfinished stories to Sprint 3 and started Sprint 3 (02:47), then opened the review, retro and planning thread in the group chat. This log records that thread.
>
> Replies (WhatsApp group; screenshots: [Josh](img/2026-10-27-sprint2-retro-reply-josh.png), [Ziyi](img/2026-10-27-sprint2-retro-reply-ziyi.png)): Josh and Ziyi each gave what went well, what didn't and what to try in Sprint 3. The screenshots show no date divider; Ziyi's reply shows 02:55. Neither asks for a change to the Sprint 3 plan. **Angelina did not reply with retro points.**
>
> Written by Carl on 2026-10-27 from Jira (sprint report and burndown), GitHub and the PR reviews. The replies and the follow-up at the end were added on 2026-11-01.

---

# Sprint Review — Sprint 2

- **Date(s):** chat thread from 2026-10-27 (no meeting)
- **Where:** Group chat
- **Participants:** Carl (facilitator); Josh and Ziyi (written replies); Angelina: no reply
- **Sprint goal:** In the browser, a signed-in customer's validated draft gets a deterministic quote; the owner approves it and the customer accepts → Order Confirmed. Start cross-order stock + schedule proposal and the ledger; first deployment from a clean zip.
- **Goal met?** Partly. The "start" half is done: sign-in (BAKE-14), cross-order stock (BAKE-19), the schedule proposal and validator (BAKE-20), the ledger (BAKE-24) and the clean-zip deploy rehearsal (BAKE-29) are merged. The main flow is not: quote approval and customer acceptance (BAKE-23) waited for ApprovalService (BAKE-15, merged 26 Oct 21:51), so no order reaches Confirmed in the browser yet.

## 1. Completed (meets DoD)

| Jira key | Story | Demoed? | Evidence (PR / commit / screenshot) |
|---|---|---|---|
| BAKE-27 | CI on every PR (Josh) | No live demo | PR #13 (23 Oct). PR #30 records the Docker build as a required check (26 Oct) |
| BAKE-19 | Stock committed to other orders, IP-02 (Carl) | No live demo | PR #16 (24 Oct) |
| BAKE-20 | Schedule proposal + validator, UC-04 1–5 (Carl) | No live demo | PR #22 (24 Oct) |
| BAKE-29 | One-command deploy from the Canvas zip (Carl) | No live demo | PR #23 (24 Oct), with PR #11 (built UI in the Docker image) |
| BAKE-14 | Auth and access control (Ziyi) | No live demo | PR #14 (25 Oct) |
| BAKE-24 | Ledger without duplicates, UC-06 1–3 (Josh) | No live demo | PR #18 (25 Oct); plan in PR #12 |
| BAKE-31 | Shortage explanation, UC-03 6 (Carl; added mid-sprint) | No live demo | PR #28 (25 Oct) |
| BAKE-41 | BAKE-8/9 review fixes (Angelina) | No live demo | PR #25 (26 Oct) |
| BAKE-15 | ApprovalService (Angelina) | No live demo | PR #24 (26 Oct). Josh approved; the lock and stale-version follow-ups were pushed after the approval and merged 6 min later |
| BAKE-18 | Order tracking, UC-02 (Ziyi) | No live demo | PR #27 (27 Oct 02:26) |

## 2. Not completed

| Jira key | Status | Reason | Decision (carry over / split / defer — who agreed) |
|---|---|---|---|
| BAKE-16 (3) | In review | PR #26 is approved by Josh but now conflicts with main in `sales/intake.py` (after BAKE-41), and with #27 in `sales/routes.py`, `test_auth.py` and `App.test.tsx` | Carry over; Ziyi resolves, keeping both sides' features and tests. Merged in Sprint 3: #26, 31 Oct |
| BAKE-22 (3) | In review | PR #19 has no approval; the explanation checker ignores a minus sign (Carl, 24 Oct), still reproducible | Carry over; Josh. Merged in Sprint 3: #19, 31 Oct |
| BAKE-23 (5) | In progress | PR #21 (draft) waited for BAKE-15; still on a stand-in for ApprovalService, no HTTP/UI yet. Reviewing it, Codex also found that retrying an acceptance after the order is Scheduled returns CONFLICT | Carry over; Josh. Merged in Sprint 3: #21 (30 Oct) and #38 (31 Oct) |
| BAKE-25 (3) | In review | PR #31 opened 26 Oct 21:54. Changes requested 27 Oct: capacity window and time zone, cancelled orders still holding capacity, stock read twice, sales by product | Carry over; Angelina. Merged in Sprint 3: #31, 31 Oct |
| BAKE-34 (3; added mid-sprint) | In progress | Profit calculation (PR #20) and the Finance page (PR #29) are merged; the AI explanation in its DoD (UC-06 5–7) is not | Carry over, or split the AI explanation into its own story. Not split: the explanation merged under BAKE-34 in Sprint 3 (#50, 31 Oct) |

Done, but with follow-up work:
- BAKE-15: no two-thread test for `decide`; two concurrent `request()` calls still raise a constraint error instead of returning the existing request (Josh's review, not blocking).
- BAKE-20: `scheduling.py` `_busy` still counts tasks of orders cancelled after release (BAKE-41 allows cancelling a Scheduled order), so a cancelled order keeps blocking new plans. Carl, with BAKE-21.

## 3. Demo notes

- No sprint demo was held, since there was no meeting on 26 Oct.

## 4. Stakeholder / tutor feedback → backlog changes

| Feedback | From | Backlog change made | Jira key(s) |
|---|---|---|---|
| BAKE-34 needed by BAKE-25 for profit figures | Josh / plan | Added to the sprint 23 Oct | BAKE-34 |
| BAKE-31 unblocked after BAKE-12/19 | Carl | Added to the sprint 25 Oct | BAKE-31 |

## 5. Metrics

- Committed at start: 44 pts · Added during the sprint: 5 pts (BAKE-34 on 23 Oct, BAKE-31 on 25 Oct) · Completed: 32 pts · Carried over: 17 pts
- Timing:
  - The burndown stayed flat at 44 from 20 Oct 03:09 to 23 Oct 19:31 (first story done: BAKE-27).
  - Done before the planned end (26 Oct 17:00), 24 pts: BAKE-27 (23 Oct 19:31), BAKE-19 (24 Oct 01:21), BAKE-20 (24 Oct 20:24), BAKE-29 (24 Oct 20:48), BAKE-14 (25 Oct 00:36), BAKE-24 (25 Oct 16:11), BAKE-31 (25 Oct 20:29).
  - Done after the planned end, 8 pts: BAKE-41 (26 Oct 21:40), BAKE-15 (26 Oct 21:52), BAKE-18 (27 Oct 02:27).
  - The two "do first" blockers landed late: BAKE-14 on 25 Oct and BAKE-15 on 26 Oct, against a Wed 21 Oct target.
  - Sprint closed in Jira at 27 Oct 02:47.
- Burndown: [img/2026-10-27-sprint2-burndown.jpg](img/2026-10-27-sprint2-burndown.jpg). Board before closing: [img/2026-10-27-sprint2-board-before-close.jpg](img/2026-10-27-sprint2-board-before-close.jpg)

---

# Retrospective — Sprint 2

- **Date(s):** chat thread from 2026-10-27
- **Where:** Group chat
- **Participants:** Josh and Ziyi (written replies), Carl (facilitator); Angelina: no reply
- **Facilitator:** Carl

## 1. Follow-up on last retro's actions

| Action from Sprint 1 | Owner | Done? | Effect |
|---|---|---|---|
| Agree shared interfaces before coding a dependent story | Everyone | Partly | Josh's plan (PR #12) listed his. ApprovalService was not agreed: BAKE-23's stand-in passed the caller's session, BAKE-15 opened its own transactions, and the mismatch only surfaced in review (24–25 Oct) |
| Small PRs with a named reviewer, no PR on an unmerged PR | Everyone | Mostly | No stacked PRs; BAKE-23 used a stand-in instead of building on #24. `main` now requires one approval and the test/check/build checks. One exception: #24's last commit was merged without a fresh review |
| Short async stand-up, move Jira cards, mid-sprint check Thu 22 Oct | Everyone; Carl | No | No stand-up records. Burndown flat for 3.7 days (target: never more than 2). BAKE-14/15 missed the Wed 21 Oct target. The Thursday check was a PR round-up in the chat (22 Oct, 03:51): it chased reviews and asked Angelina to start BAKE-15, but nothing was re-planned or cut |

## 2. What went well

- **Josh:** "CI and branch protection on main caught problems before merge, and reviews found real bugs. I finished BAKE-27, BAKE-24 and the profit calculation and Finance page for BAKE-34."
- **Ziyi:** "Sign-in and order tracking were completed, and reviews helped catch issues in the clarification flow."
- **Carl:**
  - Most of the owner-side logic landed: stock commitments, schedule proposal and validator, ledger, profitability, auth, ApprovalService and the clean-zip deploy.
  - CI now blocks merging on tests, lint and the Docker build.
  - Reviews kept finding real defects before merge (#24 transaction and race, #19 minus sign, #31 capacity and stock edge cases), each with a reproduction.

## 3. What did not go well

- **Josh:** "BAKE-23 waited on BAKE-15 until the sprint ended and my PRs sat in review, so BAKE-22, 23 and 34 carried over."
- **Ziyi:** "BAKE-16 carried over to Sprint 3, and overlapping changes made merging harder."
- **Carl:**
  - The sprint goal was missed: everything in the main flow depended on BAKE-15, which merged on the evening of the last day.
  - Work again started late: the first story was done on day 4, and 8 pts landed after the deadline.
  - Scope was added mid-sprint (BAKE-34, BAKE-31) while the blockers were still open.
  - No daily stand-ups and no review/retro meeting on 26 Oct.

## 4. Root causes (for the top 1–2 problems)

- **One blocker, one owner, no early check.** BAKE-15 was the "do first" story for BAKE-21 and BAKE-23 and had a single owner. Nobody checked its progress or interface before 24 Oct, so the delay and the interface mismatch were found three days late. Josh names the same cause: BAKE-23 waited on BAKE-15 until the sprint ended.
- **No daily signal, slow reviews.** Without stand-ups, slipping blockers were only visible when PRs appeared, and the PRs then waited for review (Josh). Overlapping changes made merging harder (Ziyi).

## 5. Improvement actions (max 3, concrete and checkable)

| Action | Owner | How we will check it next sprint |
|---|---|---|
| Daily 3-line async stand-up in the group chat (done / next / blocked). Proposed by Carl | Everyone; Carl posts the prompt | One stand-up per person per day in the Sprint 3 log |
| A blocking story posts its interface (signatures, transaction rules) in its PR on day 1; a blocked story builds against that interface instead of waiting for the merge; every review is done within 24 h of being asked. Proposed by Josh and Carl | Blocker owner; named reviewer | Interface comment dated in the first day of the story; review turnaround visible on each PR |
| Keep PRs small, check dependencies before starting, answer review comments soon, and re-request review after pushing to an approved PR. Proposed by Ziyi and Carl | PR author | No PR merged with an unreviewed post-approval commit; review comments answered within a day |

---

# Sprint Planning — Sprint 3

- **Date(s):** chat thread from 2026-10-27
- **Where:** Group chat
- **Participants:** Carl (facilitator); Josh and Ziyi replied without asking for changes; Angelina: no reply
- **Facilitator / note-taker:** Carl
- **Sprint dates:** 2026-10-27 02:47 → 2026-11-01 23:59 (Sunday, source code deadline)
- **Jira sprint:** "Sprint 3 - Demo ready" (id 5, board 2), started 2026-10-27 02:47 by Carl. Board at start: [img/2026-10-27-sprint3-board-at-start.jpg](img/2026-10-27-sprint3-board-at-start.jpg)

## 1. Inputs

- **Previous sprint:** committed 44 pts (+5 added), completed 32 pts (24 by the planned end)
- **Carried over:** BAKE-16, 22, 23, 25, 34 = 17 pts
- **Feedback to act on:** the open PR reviews above; the Sprint 2 retro actions
- **Team availability this sprint:** not collected in the thread

## 2. Sprint goal

> Every member can demo two core use cases and one optional feature in the browser on the clean-zip build, including quote approval and acceptance (UC-05) and plan approval with a one-time deduction (UC-04, IP-01).

## 3. Committed backlog (in Jira)

| Jira key | Story (short) | Points | Owner | MoSCoW | Notes |
|---|---|---|---|---|---|
| BAKE-16 | Clarification questions (carried over) | 3 | Ziyi | Must | Resolve conflicts with main |
| BAKE-17 | Owner reviews order exceptions | 5 | Ziyi | Must | |
| BAKE-38 | Optional: order status notifications | 3 | Ziyi | Must (optional) | Template-first (§11.2 step 3) |
| BAKE-22 | Quote explanation (carried over) | 3 | Josh | Must | Minus-sign fix |
| BAKE-23 | Quote approval + acceptance (carried over) | 5 | Josh | Must | Switch to ApprovalService; HTTP/UI |
| BAKE-34 | Profitability AI explanation (carried over) | 3 | Josh | Must | Or split |
| BAKE-39 | Optional: sandbox payment | 5 | Josh | Must (optional) | Local simulation only (§11.2 step 3) |
| BAKE-25 | Business summary (carried over) | 3 | Angelina | Must | Review fixes |
| BAKE-26 | Campaign plan + owner approval | 5 | Angelina | Must | After 25 |
| BAKE-35 | Promotional content validated against records | 5 | Angelina | Must | UC-08; assigned 27 Oct by module |
| BAKE-36 | Approve content versions + manual export | 3 | Angelina | Must | UC-08; assigned 27 Oct by module |
| BAKE-40 | Optional: mock social-media publishing | 3 | Angelina | Must (optional) | Immediate, owner-opted (§11.2 step 3) |
| BAKE-21 | Approve the current schedule version | 5 | Carl | Must | Unblocked by BAKE-15 |
| BAKE-32 | Record received ingredients | 2 | Carl | Must | |
| BAKE-33 | Task progress + one-time deduction | 5 | Carl | Must | After 21 |
| BAKE-37 | Optional: low-stock alerts | 3 | Carl | Must (optional) | |
| BAKE-30 | Order change / cancellation request | 5 | Ziyi | Must | UC-02; assigned 27 Oct by module |
| BAKE-28 | LangGraph spike | 3 | Angelina | Could | Assigned 27 Oct by module; first to cut (§11.2 step 1) |

**Total committed:** 69 pts in 18 stories (velocity 32). Per person: Angelina 22, Josh 16, Ziyi 16, Carl 15. Nothing cut yet; if behind at the Thursday check, cut in the §11.2 order (BAKE-28 first, then polish, then simplify, not drop, the optional features).

## 4. Risks and Definition-of-Done reminders

- Common DoD: reviewed PR with the Jira key in the title, automated tests pass, no secrets committed.
- Risks:
  - Scope is about twice the observed velocity, in a 6-day sprint.
  - BAKE-23 → BAKE-39 and BAKE-21 → BAKE-33 are chains; the first link must land early.
  - Angelina carries 22 pts, the most of anyone, including all of UC-07/08.
  - Demo time in Week 13 is not announced yet.

## 5. Decisions

- Carl closed Sprint 2 and started Sprint 3 before the replies, as in Sprint 2, so no day is lost; changes from the thread will be applied in Jira and noted here.
- Sprint 3 ends Sun 1 Nov 23:59, the source code deadline.
- No scope cut at the start. The size is flagged in the group message, and the Thursday check decides whether to cut in the §11.2 order.
- The unowned stories were assigned by module, following the architecture's ownership table: BAKE-30 to Ziyi; BAKE-28, 35 and 36 to Angelina.

## 6. Action items

| Action | Owner | Due | Jira key |
|---|---|---|---|
| Reply with retro points and OK / changes to the Sprint 3 plan | Everyone | Josh and Ziyi replied; Angelina did not | — |
| Close Sprint 2, move unfinished stories, start Sprint 3 | Carl | Done 2026-10-27 02:47 | BAKE-16, 22, 23, 25, 34 |
| Assign the unowned stories by module | Carl | Done 2026-10-27 | BAKE-28, 30, 35, 36 |
| Thursday check: cut or not | Carl | Done Thu 29 Oct in the group chat; no cut | — |
| Resolve #26 conflicts | Ziyi | Done; #26 merged 31 Oct | BAKE-16 |
| Fix the minus-sign check in #19 | Josh | Done; #19 merged 31 Oct | BAKE-22 |
| Address the #31 review | Angelina | Done; #31 merged 31 Oct | BAKE-25 |

---

# Follow-up (added 2026-11-01)

- **Thursday check, 29 Oct (group chat).** Every open PR passed CI and merged cleanly into main. The most urgent work was BAKE-23 (#21) and then BAKE-22 (#19), since only they produce a real Confirmed order. Nothing was cut; Angelina was asked whether to keep BAKE-28.
- **Carried-over stories.** All five merged in Sprint 3: BAKE-16 (#26), BAKE-22 (#19), BAKE-23 (#21, #38), BAKE-25 (#31) and BAKE-34 (#50).
- **BAKE-28 dropped (1 Nov).** It was moved out of Sprint 3 to the backlog as not adopted, the first cut in the §11.2 order. The reasons are on the Jira card: it is a Could, nobody had started it, no mandatory flow depends on it, and the current one-call-per-operation design already runs end to end with a live model.
- **Sprint 3 closed (1 Nov, 21:37).** All 20 stories in the sprint were done, 78 pts: the 69 pts committed at the start, less BAKE-28 (3), plus BAKE-42, 43 and 44 (12), which were added during the sprint. Burndown: [img/2026-11-01-sprint3-burndown.jpg](img/2026-11-01-sprint3-burndown.jpg). Board before closing: [img/2026-11-01-sprint3-board-before-close.jpg](img/2026-11-01-sprint3-board-before-close.jpg). The Sprint 3 review and retro are not part of this log.
