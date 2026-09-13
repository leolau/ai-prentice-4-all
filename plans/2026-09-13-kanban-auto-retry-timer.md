# Feature: an automatic retry timer for a blocked card

Prompted by a project card ("Execute prompts in Canva") legitimately
hitting Canva's daily generation limit and sitting `blocked` forever with
no one able to act on it until a human noticed and clicked "Make ready" —
sometimes a full day later, i.e. exactly when the quota would have reset
anyway.

## Design

`kanban_db.block_task()` already has a `kind="transient"` — "a flaky
failure that may clear on retry" — but nothing ever *acted* on that
semantic; every block, transient or not, just sat in `blocked` until a
human intervened. The fix wires up the "may clear on its own" half of that
kind:

1. **`block_task(..., retry_after_seconds=N)`** stamps a new `retry_at`
   column (`now + N`) on a task that actually lands in `blocked` (not on
   the `dependency` → `todo` or loop-detected → `triage` routes — neither
   is a "wait it out" state; the triage path explicitly clears it).
2. **`auto_retry_timed_blocks(conn, now=...)`** — new function. Finds every
   `blocked` task whose `retry_at` has passed and unblocks it via the
   *existing* `unblock_task()` path (parent-gate check, run/claim cleanup),
   tagged `auto=True` so the event is `auto_retry`, not `unblocked` — board
   history / notifications can tell "a human clicked" apart from "the
   timer fired".
3. **Wired into `dispatch_once` → `_dispatch_once_locked`**, first thing
   every tick, for every board — the same loop that already runs
   `release_stale_claims`/`recompute_ready`. Zero new background
   loop/thread/watcher; this is the "extend existing code" rung of the
   footprint ladder. `DispatchResult.auto_retried` surfaces which tasks
   fired, same shape as `auto_blocked`/`rate_limited`.
4. **Loop safety is not new** — it's the *existing* unblock-loop breaker.
   If the worker hits the same wall again right after an auto-retry, that
   re-block still counts as a same-cause recurrence and still escalates to
   `triage` at `BLOCK_RECURRENCE_LIMIT` (2), exactly as a human/cron
   unblock would trigger it today. A wrong retry-time guess costs one
   extra wasted attempt, not an infinite loop.
5. **Tool surface** (`tools/kanban_tools.py::KANBAN_BLOCK_SCHEMA` /
   `_handle_block`): `kanban_block` gained an optional
   `retry_after_seconds` integer parameter, documented as "for a block
   that will clear on its own — a daily/hourly API quota, a rate limit".
   The model calling it for the Canva case would pass
   `kind="transient", retry_after_seconds=86400`.
6. **Notifications** (`gateway/kanban_watchers.py`): `auto_retry` joins
   `TERMINAL_KINDS` (so subscriptions advance past it and survive it, same
   as `gave_up`/`crashed`/`timed_out`) and gets its own message — unlike a
   human-driven `unblocked` (silent; they already know), an auto-retry IS
   worth a ping since nobody touched the card.
7. **CLI visibility** (`hermes_cli/kanban.py`): `hermes kanban dispatch`
   (JSON and human output) and the standalone `daemon --force` verbose
   tick log both surface `auto_retried` alongside the existing
   `auto_blocked`/`reclaimed`/etc. counters.

No new config, no new core model tool, no new API endpoint — a new
optional parameter on an existing tool, a new column, and one more line in
an existing per-tick sweep.

## What this does NOT do

- It does not retroactively add a timer to a card that's already sitting
  blocked from before this change (its `retry_at` is `NULL` — same as
  every existing blocked card). Fixing the specific already-blocked "Execute
  prompts in Canva" card the report was about is a live operational action
  (either a human clicks "Make ready" once the quota actually resets, or
  someone with access to that box runs an unblock with a stamped
  `retry_at`) — out of scope for a code change in this worktree, which has
  no access to that deployment's database.
- It does not touch the agent-home UI. The card detail page already shows
  the block reason (fixed separately, PR #392) and the existing "Make
  ready" button; nothing about the auto-retry needed a UI change — the
  card just quietly moves on its own and the existing page reflects
  whatever status it lands on.

## Verification

- `tests/hermes_cli/test_kanban_block_kinds.py` — 8 new tests: timer
  stamped correctly, validation (rejects `<= 0`), auto-retry fires only
  once due, leaves untimed blocks alone, still escalates a genuine loop to
  `triage`, manual unblock clears a pending timer, and a real
  `dispatch_once` tick (not just a direct function call) picks it up.
- `tests/tools/test_kanban_tools.py` — 2 new tests: `retry_after_seconds`
  round-trips into `retry_at` via the tool handler, non-positive/non-numeric
  values are rejected.
- `tests/hermes_cli/test_kanban_notify.py` — 1 new test: the notifier sends
  an "auto-retried" message and keeps the subscription alive.
- Full per-file isolated run (`.venv/bin/python -m pytest <file> -q` per
  `scripts/run_tests.sh`'s own documented isolation policy — a combined
  multi-file invocation hit a pre-existing, unrelated cross-file state leak
  in `test_detect_crashed_workers_protocol_violation_auto_blocks`,
  reproduced identically with this change fully reverted): all of
  `test_kanban_db.py`, `test_kanban_block_kinds.py`, `test_kanban_notify.py`,
  `test_kanban_per_profile_cap.py`, `test_kanban_default_assignee.py`,
  `test_kanban_core_functionality.py`, `test_kanban_dispatch_lock.py`,
  `test_kanban_cli_dispatch_passthrough.py`, `test_kanban_tools.py`,
  `test_kanban_watchers_mixin.py` pass (573 tests total).
- `test_signal_handler_kanban_worker.py::test_sigterm_with_kanban_task_env_terminates_quickly`
  fails in this sandbox both with and without this change (subprocess
  signal-timing flake unrelated to kanban_db logic) — pre-existing, not
  caused by this change.
