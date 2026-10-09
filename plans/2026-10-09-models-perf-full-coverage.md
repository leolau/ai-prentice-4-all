# Models ▸ Performance — collect stats on all fronts

Status: **implemented** — pending PR/deploy

## Problem

`api_call_log` (shipped in #514) only records `caller='main'` rows —
5,564 rows on prod and zero aux rows. Three gaps:

1. **Aux bypass**: `call_llm`/`async_call_llm` wrappers were instrumented,
   but many aux consumers (`kanban_specify`, `kanban_decompose`, `goals`
   judge/draft, `profile_suggestion`, `profile_describer`, vision tools,
   compression) fetch raw clients via `get_text_auxiliary_client` /
   `resolve_vision_provider_client` / `resolve_provider_client` and call
   `client.chat.completions.create()` directly — never wrapped.
2. **Task label can't be bound at client-build time**: the client cache
   key only includes `task` for `provider=="auto"` — explicit-provider
   clients are shared across tasks, so a creation-time label would
   misattribute (e.g. compression's client later serving goal_judge).
3. **Per-profile split**: kanban workers run `-p <assignee>` and write to
   `profiles/<name>/state.db`; the performance endpoint reads one DB only.

## Design

### ContextVar + idempotent `create` wrap (auxiliary_client.py)

- `_AUX_TASK_CTX: ContextVar` — the aux task label for the *current call*,
  read by the shim at `create()` invocation time (correct under client
  sharing and for stream/abandon cases — label snapshots before iteration).
- `_ensure_logged_create(client, provider, model_hint)` — replaces
  `client.chat.completions.create` with a timed shim (marked idempotent).
  Records `caller='aux:<ctx task>' or 'aux'`. Sync/async detected via
  `iscoroutinefunction`. `stream=True` returns a `_LoggedStream` /
  `_LoggedAsyncStream` proxy that records on exhaust/close (captures the
  final `usage` chunk when present).
- Applied at:
  - `_get_cached_client` before caching — covers `call_llm`,
    `async_call_llm`, and every consumer whose resolution lands in the
    cache funnel.
  - `resolve_provider_client` only when `task` is set — covers direct
    aux callers without touching main-path construction
    (`agent_init`, `chat_completion_helpers` pass no task → unwrapped,
    no double-count with the loop's own ledger write).
- `_task_scoped_client(client, task)` proxy returned by
  `get_text_auxiliary_client`, `get_async_text_auxiliary_client`,
  `resolve_vision_provider_client` — sets `_AUX_TASK_CTX` around `create`
  (async variant awaits inside the scope so the var is visible to the
  shim). Zero call-site changes for `kanban_specify`/`goals`/etc.
- `call_llm`/`async_call_llm` wrappers shrink to ContextVar set/reset
  (their own recording removed — the shim owns writes; keeps
  `functools.wraps` signature fix).

### Multi-home aggregation (web_server.py)

`GET /api/analytics/models/performance` with `profile=None` now unions
`api_call_log` + `sessions` rows across the default home and every
`profiles/*/state.db` (read-only connects, skip missing tables). An
explicit `profile` param still scopes to that home. Aggregation moves
fully into Python (rows are bounded — days ≤ 30, months ≤ 24) so merging
per-DB result sets is uniform.

## Tests

- shim records ok/error/stream rows (sync + async); idempotent re-wrap
- **ctx-gated recording**: `_AUX_TASK_CTX` unset → pure passthrough, zero
  rows — this is what makes wrapping safe even where a client shape leaks
  toward main-path code (`_to_async_client` conversions, shared caches)
- `_to_async_client` mints fresh AsyncOpenAI/adapter clients — a
  `@_logged_async_conversion` decorator re-wraps its output (the wrap on
  the source sync client does not carry over)
- `_refresh_nous_auxiliary_client` writes fresh clients into the cache
  directly — wrapped before `_store_cached_client`
- provider/model hints upgrade in place: a later `_ensure_logged_create`
  with better hints fills empty slots on an already-bound wrapper
- `call_llm` end-to-end → exactly one row, `aux:<task>` caller;
  ContextVar reset afterwards (no leakage)
- `get_text_auxiliary_client` proxy → row labelled with the task
- endpoint unions two homes (default + a profile DB)
- regression: `test_auxiliary_client.py` (290), `run_agent` (420),
  vision/goals/kanban-specify consumers (121) — all green
