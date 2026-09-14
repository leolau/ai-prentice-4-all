# Yes, it can stream — plus elapsed time either way

## Question

On the card page, the "What's happening" panel said "its reasoning isn't
streamed here" for a running, board-dispatched card. Asked: can it
actually be streamed? If not, can the page at least show how long it's
been working?

## Answer: yes, it can — the log file was already there

A board-dispatched card's worker has no *in-memory* reasoning buffer to
tail the way an inline Projects run's session does (`run_activity.py`,
which only ever works within the one process that's running the model
loop). But `kanban_db.py`'s `_default_spawn` already redirects the
worker's stdout/stderr to a durable per-task file —
`<board-root>/logs/<task_id>.log` — precisely so a *different* process
(the dashboard's `GET /tasks/{id}/log`, already existed) can read it.
That file is the model's own sentences and tool-call summaries, just
formatted for a real terminal: ANSI colour codes, spinner frames
re-drawn in place via a bare `\r`, and decorative box-drawing borders
around each turn.

## Fix

**`hermes_cli/kanban_db.py`**: `worker_log_plain_tail(task_id, board=,
max_chars=)` — reads the log (`read_worker_log`, already existed) and
strips the terminal-only noise:
- ANSI escape sequences.
- The decorative box-drawing separator lines and the "╭─ ⚕ Hermes ─╮"
  turn banner (pure chrome, no content).
- Spinner-frame repeats: a tool-call line redrawn many times with only
  its trailing elapsed suffix changing (`grep  0.1s` → `grep  0.4s` → …)
  collapses to one line, keeping the *last* (settled) value.

Returns the tail (most recent `max_chars`), or `None` before the worker
has written anything usable.

**`hermes_cli/projects_api.py`**: `_card_payload` (shared by the plain
card GET and the SSE push stream — §12, no extra wiring needed) attaches
`worker_log_tail`.

**`agent-home`**:
- `types/index.ts` — `worker_log_tail?: string | null` on
  `ProjectCardDetail`.
- `CardDetailView.tsx` — the "What's happening" panel now shows the
  cleaned log tail in a scrollable monospace block when present, and a
  **ticking elapsed-time counter** ("working for 3m 20s") next to the
  heading the whole time the card is `running` — the second ask, and the
  one thing that's always available even before the first heartbeat or
  log line arrives. The elapsed clock mirrors the chat pane's own
  1-second tick-while-active pattern; it costs nothing extra over the
  wire, it's a local timer against `started_at`.

## Explicitly not built

- No ANSI-to-HTML rendering (colour, bold, etc.) — stripped rather than
  converted. The plain text (reasoning sentences + tool summaries +
  diffs) reads fine on its own; colour would be a pure nicety for a
  meaningfully larger implementation (a real ANSI parser).
- No structured tool-call rendering (chips, icons) the way the inline
  run activity stream has (`LiveActivity.tsx`) — that reads typed SSE
  events (`tool.start`/`tool.complete`); this is free-text log lines.
  Giving the worker CLI itself a structured event sink instead of a
  plain stdout redirect would be the real fix for that gap, and is a
  much larger change than this report asked for.
