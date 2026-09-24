# Projects: live updates by server push (SSE), not client polling

## Problem

User report: "the UI seems very unresponsive" on `agent-home`, asking
whether client-side polling was the cause and whether WebSocket would help.

Audit of the whole app found polling was **not** pervasive — chat itself
(the core surface) already streams over SSE, both the assistant's reply and
a live inline run's reasoning/tool activity (`hermes_cli/run_activity.py` +
`useRunActivity.ts`). Client-side `setInterval`/`setTimeout` polling was
isolated to the Projects surfaces:

| Surface | Old interval | Mechanism |
|---|---|---|
| Project detail page (`useProjectEvents`) | 15s | fetch cursor → `router.refresh()` |
| Run page (`useRunLive`) | 5s | fetch → `setState` |
| Card page (`useCardLive`) | 5s | fetch → `setState` |

So the "unresponsive" feeling was real but narrow: up to 5–15s of staleness
on these three pages, by design (a fixed poll interval), not a systemic
polling problem.

## Why SSE, not WebSocket

WebSocket's only advantage over SSE is a duplex channel — this app never
needs the browser to push data back over the live channel (actions stay
ordinary POSTs). SSE gets the same "instant" push with far less new
infrastructure, and the codebase already has a working, proven SSE pattern
(`run_activity_route` / `useRunActivity.ts` / `readSseFrames`) to extend
rather than invent. Adopting WebSocket would also require a
persistent-connection-friendly server; Next.js API routes handle a
streaming `Response` (SSE) natively without that requirement.

The actually-hard part — cross-process change notification, since a card's
worker or the dispatcher can mutate the board from a different OS process
than the one serving a browser's connection, and SQLite has no
`LISTEN/NOTIFY` — is identical for SSE and WebSocket. Both still need a
server-side watch loop; SSE just avoids paying for a new transport on top
of that.

## Design

**Backend** (`hermes_cli/projects_api.py`): a shared tick-and-diff loop,
`_watch_stream()`, behind three new routes:

- `GET /{slug}/runs/{run_no}/stream` — the same payload `GET
  /runs/{run_no}` returns (via the existing `_run_payload` helper), ending
  once the run reaches a terminal status (`done`/`failed`/`cancelled`,
  matching `useRunLive.ts`'s old `TERMINAL` set exactly).
- `GET /{slug}/cards/{task_id}/stream` — the same payload `GET
  /cards/{task_id}` returns (factored out into a new `_card_payload`
  helper shared by both routes, so they can never drift), ending once the
  card leaves `running`.
- `GET /{slug}/events/stream` — the same `latest_event_id` cursor `GET
  /events` returns, never ending on its own (a project has no terminal
  state) — only a client disconnect stops it.

`_watch_stream()` re-reads the row on a 1s local tick (cheap — it's a
local SQLite read), diffs the serialised JSON against the last frame sent
so a quiet row doesn't spam the wire, and sends a bare SSE comment every
20s of no change so a reverse proxy's idle-connection timeout never closes
a quiet stream out from under a long-open tab. A truly unknown run/card is
still a plain 404 (checked before the stream opens), matching the plain
GET routes and `run_activity_route`'s own convention; a row that
disappears **after** the stream has already started (rare) is a `gone`
frame instead, since the HTTP status can't change at that point.

**Frontend**: a new shared `useRowStream` hook
(`agent-home/src/components/projects/useRowStream.ts`) opens the SSE
stream, reconnects with capped backoff (`500ms → 1s → 2s → 5s → 10s`) on
an unexpected drop, and stops for good on an `end`/`gone` frame. The
connect/reconnect algorithm is split into a plain `openRowStream()`
function so it's unit-testable without a DOM or real timers.

`useRunLive`, `useCardLive` and `useProjectEvents` keep their exact old
call signatures (`useRunLive(slug, runNo, status, onRun)` etc.) — only
their internals changed, from a poll timer to `useRowStream` — so
`RunView.tsx`, `CardDetailView.tsx` and `ProjectDetailView.tsx` needed no
changes at all.

**`router.refresh()` on the project page**: kept as-is. It's Next.js's own
idiomatic way to re-fetch a route's server-rendered data without a full
page reload or losing client state — the real problem was the 15s
*latency* before it fired, not the mechanism itself. SSE fixes that
directly (a movement now reaches the page in about a second); no other
change was needed there.

## Files

- `hermes_cli/projects_api.py` — `_watch_stream`, `_card_payload`
  (factored out of `get_card`), `run_stream_route`, `card_stream_route`,
  `project_events_stream`.
- `agent-home/src/lib/api/client.ts` — `openRunStream`, `openCardStream`,
  `openProjectEventsStream` (+ shared `_openRowStream`).
- `agent-home/src/app/api/projects/.../stream/route.ts` (3 new BFF proxy
  routes, mirroring `runs/[runNo]/activity/route.ts`).
- `agent-home/src/components/projects/useRowStream.ts` (new, shared).
- `agent-home/src/components/projects/useRunLive.ts`,
  `useCardLive.ts`, `useProjectEvents.ts` — rewritten internals, same
  public signatures.

## Explicitly not done

- No WebSocket anywhere — see "Why SSE, not WebSocket" above.
- No change to `RunView.tsx` / `CardDetailView.tsx` / `ProjectDetailView.tsx`
  beyond what their unchanged hook calls already picked up for free.
- No new message broker / pub-sub layer — the watch loop reads SQLite
  directly, same as the polling it replaces, just moved server-side and
  ticking faster (1s vs. the old 5-15s client poll).
