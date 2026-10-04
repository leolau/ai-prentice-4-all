# Blocked-checkpoint runs must never auto-fail + honest idle labels

## Problem (all from one screenshot of the MOU project)

1. **"Run 2 failed"** — the run was auto-failed by the stale-run sweep
   while its `review-drafts` checkpoint card sat `blocked` with a
   `review-required:` reason — the deliberate human-review handoff the
   worker prompt teaches. `checkpoint_wait_info` only recognised a hold
   when the checkpoint step was `done` and its successors sat in
   `triage`; a checkpoint card itself in `blocked` (or `triage` with
   settled deps) slipped through, so the sweep saw "nothing in flight +
   stale >2h" and failed a correctly paused run. Every card then
   completed under a "failed" record.
2. **"14 of 18 steps done"** — with no open run the bar counts the whole
   board (`card_rollup`), including 4 replan cards that never ran —
   labelled "steps" it read as run progress.
3. **Idle "next for you"** — ignored the 3 cards sitting in Triage
   awaiting approval (the actual next action) and only spoke about the
   last run's failure.

## Fix

`hermes_cli/projects_run.py` — `checkpoint_wait_info` additionally treats
as held: a checkpoint step whose card is `blocked`, or in `triage` with
its own deps settled. Causing/held sets union the engaged checkpoints.
Effect: sweep reports `awaiting_continue` instead of `failed_stale`, and
the run page's `awaiting_continue` flag correctly shows "Needs you".

`agent-home` (`liveState.ts`, `LiveStatusBar.tsx`):

- `countScope: "run" | "board"` on the live state; the progress label
  reads "steps done" for an open run's steps, "cards done" for the board
  rollup.
- Idle `nextForYou` prioritises the actionable thing: "approve N cards
  waiting in Triage, then start the next iteration" ahead of the last
  run's status.

## Data repair (done on the box, 2026-10-05)

`project_runs` `run_f88e59bc8621` (MOU project run 2): status
`failed → done`, outcome notes the watchdog misfire and repair. All 8 of
its cards were in fact `done`.

## Verification

- `test_projects_reconcile.py` — new incident regression (blocked
  checkpoint card → sweep reports `awaiting_continue`, run stays
  `running`, `checkpoint_wait_info` names the blocked step): 11/11 pass.
- vitest live suite: 34 pass; tsc + eslint clean.

## Status

- [x] Watchdog fix + regression test
- [x] Data repair (run 2 record)
- [x] UI labels
- [x] Merged (PR #499) / deployed `ce98398f0`
