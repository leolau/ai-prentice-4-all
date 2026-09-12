# Bug: a blocked project card shows no reason and no next step

Reported via screenshot: `/projects/[slug]/cards/[id]` for a card whose
status is `blocked` showed the status pill and "Make ready" / "Edit card" /
"Move to..." — nothing else. No reason, no hint of what the user is
supposed to answer or do.

## Root cause

`kanban_block` (the tool the agent calls to stop work and hand a card to a
human) *requires* a `reason` string
(`tools/kanban_tools.py::KANBAN_BLOCK_SCHEMA`, enforced in the handler:
"reason is required — explain what input you need"). That reason is stored
via `kanban_db.block_task(..., reason=reason)` → `_end_run(..., summary=reason)`
→ `task_runs.summary`.

The **board list** endpoint (`hermes_cli/projects_api.py`, board handler)
already surfaces this correctly: it batch-fetches
`kanban_db.latest_summaries()` and passes it into
`kanban_view.task_dict(task, latest_summary=...)` for every card.

The **single-card detail** endpoint — `GET /{slug}/cards/{task_id}` /
`get_card()`, the one this exact page calls — called
`kanban_view.task_dict(task)` with **no** `latest_summary` argument at all.
`latest_summary` defaults to `None`, so the reason a worker was required to
give never reached this page, regardless of whether one was recorded.
`CardDetailView.tsx`'s "Latest from the worker" section is conditioned on
`card.latest_summary || card.result` — both were empty, so the whole
section silently didn't render. That's the exact screenshot: a blocked
card with nothing to explain it.

## Fix

1. **`hermes_cli/projects_api.py::get_card()`** — fetch
   `kanban_db.latest_summary(bconn, task_id)` and pass it through, mirroring
   the board endpoint. This alone makes the reason reach the API response.
2. **`agent-home/src/types/index.ts`** — added `block_kind` to
   `ProjectBoardTask` (the field was already in the API response via
   `asdict(task)`, just not typed, so the frontend never read it).
3. **`agent-home/src/components/projects/CardDetailView.tsx`** — a blocked
   card now gets a dedicated, prominent banner (not folded into the
   generic "Latest from the worker" section, which is now suppressed for
   `blocked` cards to avoid showing the same sentence twice):
   - A label from `block_kind` ("Needs your input" / "The agent can't do
     this — needs a human" / "Hit a snag — may clear on retry" / generic
     "Blocked" for a legacy/un-typed block).
   - The reason text itself.
   - A one-line hint pointing at the two controls already on the page
     (Edit card → answer/adjust the brief, then Make ready) — no new
     affordance needed, just telling the user the existing ones are the
     answer.

No new API surface, no new UI controls — this is entirely "data existed,
wasn't wired to the one endpoint that needed it, and wasn't framed clearly
once it was."

## Verification

- New backend regression test:
  `tests/hermes_cli/test_projects_api.py::test_card_detail_surfaces_the_block_reason`
  — blocks a card with `kind="needs_input"` and a reason, asserts the card
  detail response includes both `latest_summary` and `block_kind`.
- New frontend tests in `ProjectDetailView.test.tsx` (`CardDetailView`
  describe block): the banner renders the kind label + reason + "Make
  ready", doesn't duplicate into `CardResult`, and still renders (with a
  fallback line) when no reason was recorded.
- `./.venv/bin/python -m pytest tests/hermes_cli/test_projects_api.py
  tests/hermes_cli/test_kanban_db.py -q` — 260 passed.
- `cd agent-home && npm run typecheck` — passes.
- `cd agent-home && npx vitest run` — 617 passed (14 pre-existing,
  unrelated jsdom/ESM environment failures in other files, not touched by
  this change).
