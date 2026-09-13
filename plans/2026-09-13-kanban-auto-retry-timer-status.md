# Implementation status: auto-retry timer for blocked cards

Companion to `plans/2026-09-13-kanban-auto-retry-timer.md`.

| # | Description | Status | Notes |
|---|-------------|--------|-------|
| 1 | `tasks.retry_at` column (schema + migration) | **Done** | `hermes_cli/kanban_db.py` |
| 2 | `block_task(..., retry_after_seconds=...)` stamps `retry_at` | **Done** | Cleared on the triage-escalation route; never set on the dependency→todo route |
| 3 | `auto_retry_timed_blocks()` + `unblock_task(..., auto=True)` tagging | **Done** | `retry_at` always cleared on any unblock (manual or auto) |
| 4 | Wired into `dispatch_once` → `_dispatch_once_locked`, `DispatchResult.auto_retried` | **Done** | Runs first in the tick, before reclaim/promote |
| 5 | `kanban_block` tool: `retry_after_seconds` param + validation | **Done** | `tools/kanban_tools.py` |
| 6 | Gateway notifier: `auto_retry` event → its own message, kept alive like `gave_up`/`crashed` | **Done** | `gateway/kanban_watchers.py` |
| 7 | `hermes kanban dispatch` CLI + daemon verbose tick log surface `auto_retried` | **Done** | `hermes_cli/kanban.py` |
| 8 | Tests (kanban_db, tool handler, notifier, real dispatch_once tick) | **Done** | See plan doc's Verification section |
| 9 | Retroactively fix the already-blocked "Execute prompts in Canva" card | **Not done — out of scope for this worktree** | No access to that deployment's database from here; needs a live operational action (human "Make ready" once quota resets, or an operator-run unblock with a stamped `retry_at`) |

## How to update this file

Row 9 is the only open item, and it's operational, not a code change — if
someone applies it against the live box, note the action taken here (or
just resolve it by clicking "Make ready" once the quota actually clears,
per the card's own detail page fix in PR #392).
