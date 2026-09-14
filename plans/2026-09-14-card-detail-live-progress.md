# Card detail page: show live progress + reasoning for a running card

## Problem

A user reported: the card page for a `running` card ("Review tender document
and extract all requirements") showed `Stop` / `Re-run` and the brief, but
**no progress update and no reasoning info** — the page looked exactly the
same whether the worker had just started or had been running for an hour.

Root cause, confirmed by reading the code:

1. `agent-home/src/components/projects/CardDetailView.tsx` had no `"use
   client"` directive and no hooks — it was a plain server component,
   rendered once by `app/projects/[slug]/cards/[id]/page.tsx` (itself
   `force-dynamic`, but only re-evaluated on navigation/reload). Nothing on
   the page ever re-fetched while sitting open.
2. Even a manual reload wouldn't have helped much: `GET
   /{slug}/cards/{task_id}` (`projects_api.py::get_card`) returned
   `kanban_view.task_dict(task, latest_summary=summary)`, where
   `latest_summary` is `task_runs.summary` from the most recently **closed**
   run — nothing from the worker while it is still actively running. A
   worker's periodic self-reported progress (`kanban_db.heartbeat_worker(...,
   note=...)`, the exact mechanism that produced comments like "93/131
   slides completed…" during the Canva incident) and its narrated comments
   (`kanban_db.add_comment`) were never read by this endpoint at all.

A board-dispatched card's worker runs in its own OS process — unlike an
inline Projects run's session (`hermes_cli/run_activity.py`), there is no
live reasoning/tool-call stream available for it. The heartbeat note +
comment thread are the actual signal that already exists for this case; the
page just never read or displayed them.

## Fix

**Backend** — `hermes_cli/projects_api.py::get_card()`: attach two more
fields to the existing payload, both already-existing read paths
(`kanban_db.list_comments`, `kanban_db.list_events`), so no new tables/writes
are needed:

- `comments`: the full comment thread (`author`, `body`, `created_at`).
- `latest_heartbeat`: the most recent `heartbeat` event that carried a
  `note`, or `null`.

**Frontend**:

- `agent-home/src/components/projects/useCardLive.ts` (new) — a lean
  per-card poller mirroring `useRunLive.ts`'s `createRunPoller`/`isRunLive`
  shape: `createCardPoller` + `isCardLive` (pure, unit-tested) plus the
  `useCardLive` hook. Only `running` is live — every other status is either
  not-yet-started or already closed and won't move on its own; polling
  stops the moment the card leaves `running`.
- `agent-home/src/components/projects/CardDetailView.tsx` — converted to a
  client component (`"use client"` + local `useState` seeded from the
  server-rendered `card` prop), wired to `useCardLive`. Added:
  - a **"What's happening"** panel, shown only while `running`: the latest
    heartbeat note + "updated Xh ago" (or an honest "waiting for its first
    update" placeholder), with a spinner — explicitly *not* claiming a live
    reasoning stream that doesn't exist for this surface.
  - an **"Updates"** panel: the comment thread, newest first — the fuller
    narrative progress notes a worker posts along the way.
- `agent-home/src/types/index.ts` — `ProjectCardComment`,
  `ProjectCardHeartbeat`, and both added (optional) to `ProjectCardDetail`.

## Explicitly not done

- No new live reasoning/tool-call stream for board-dispatched cards — that
  infrastructure (`run_activity.py`) is specific to inline Projects-run
  sessions with a `session_id`; a kanban worker is a separate process with no
  equivalent hook today. Building one is a much larger change than this
  report asked for; flagged as a possible follow-up, not attempted here.
- No change to the board list endpoint (`GET /{slug}/board`) — only the
  single-card detail page needed this; the board already shows enough at
  the row grain.
