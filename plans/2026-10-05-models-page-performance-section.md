# Models page — Performance section (agent-home)

Status: **implemented** (branch pending PR)

## Implementation status

- `api_call_log` table + indexes in `SCHEMA_SQL`; `record_api_call()` +
  `prune_api_call_log()` on `SessionDB`; `api_metrics_db()` module helper
  (bounded per-`HERMES_HOME` cache) + `record_api_call()` module fn —
  `hermes_state.py`. ✅
- Writers: `conversation_loop.py` success path (unconditional, beside the
  `post_api_request` emit), `run_agent.py::_invoke_api_request_error_hook`
  (before the `has_hook` early-return), `agent/auxiliary_client.py`
  `call_llm`/`async_call_llm` telemetry wrappers (`functools.wraps`
  preserves signatures; stream=True returns unlogged). ✅
- `GET /api/analytics/models/performance?days=7&months=6` in
  `web_server.py` — 7-day per-model stats (calls, failures, success rate,
  min/avg/p95/max, tokens, cost from `sessions`), daily buckets, monthly
  series joined with `sessions` so pre-ledger months still show
  calls/tokens with `avg_ms`/`failures` null. ✅
- BFF `GET /api/models/performance` + `client.modelsPerformance()` +
  `ModelsPerformanceResponse` types. ✅
- `ModelPerformance` section in `ModelsView` (lazy fetch, after Task
  roles): per-model cards w/ 7-day call bars, history chart with
  `tokens | calls` toggle, collecting empty state. ✅
- Tests: `tests/hermes_state/test_api_call_log.py`, endpoint tests in
  `test_web_server.py`, hook-funnel tests in `test_run_agent.py`,
  `ModelPerformance.test.tsx`, interaction-mock updates. ✅

### Known limits

- Latency/failure data accrue from deploy forward; `collecting` flag
  drives the empty state until then.
- The full `tests/agent`+`tests/hermes_state` suite has ~106 pre-existing
  environmental failures on this machine (keychain mocks, platform
  detection) — identical set on clean `develop`; delta introduced by this
  change: zero (one signature-inspection regression found and fixed via
  `functools.wraps`).

## Requirement

Inside `agent-home` Models page (`/models`), add a Performance section
showing, **per configured+used model**: call count, failure stats,
min/max/avg (and p95) response time, token consumption, cost — for the
**last 7 days** — plus a **monthly history chart** for the past ~6 months.

## What's already there

- `agent-home/src/components/models/ModelsView.tsx` — sections today:
  Main model → Task roles → "In use — last 30 days" (sessions / tokens /
  cost per model) → Pinned cards. Data: SSR fan-out in
  `src/app/models/page.tsx` → `/api/models/overview` → Python endpoints.
- `web_server.py: GET /api/analytics/models?days=N` — aggregates the
  per-profile `sessions` table (`hermes_state.py`): model,
  billing_provider, tokens (in/out/cache/reasoning), costs,
  `api_call_count`, `tool_call_count`, timestamps. Per-SESSION grain —
  good for calls/tokens/cost history, **has no per-call latency or
  failure data**.
- Per-call telemetry already exists as plugin hooks —
  `post_api_request` (api_duration, usage, finish_reason) and
  `api_request_error` (error_type, status_code, retry_count, duration) —
  but they only fire when a plugin subscribes and nothing persists them.
- Aux-role calls (vision, compression, title gen, …) bypass
  `conversation_loop` — they go through `agent/auxiliary_client.py`
  (returns `usage` on each call). Any recorder must cover BOTH paths or
  the page lies about aux models.
- Chart convention: no chart lib. `CapacityView.tsx` `CpuHistorySection`
  hand-rolls CSS flex bars + `title` tooltips. Follow that.

## Design

### 1. `api_call_log` table (SessionDB, per-profile sqlite)

Sibling of `sessions`, same idempotent-migration pattern:

```
id INTEGER PK, ts REAL, session_id TEXT, model TEXT, provider TEXT,
caller TEXT,            -- 'main' or 'aux:<task>' (vision, compression…)
duration_ms INTEGER, status TEXT,       -- 'ok' | 'error'
finish_reason TEXT, error_type TEXT, status_code INTEGER,
input_tokens INT, output_tokens INT, cache_read INT, reasoning INT,
retry_count INT
```

Index on `(ts)`, `(model, ts)`. One row per provider call — prod volume is
hundreds/day, trivial. Prune rows >365d lazily.

### 2. Recorder — `SessionDB.record_api_call(...)`, always-on

Fire-and-forget (try/except, never blocks the loop). Called at the same
points the hooks emit, NOT inside `has_hook()` guards:

- `conversation_loop.py` success path (~the `post_api_request` emit site)
- `run_agent.py::_invoke_api_request_error_hook` (single error funnel —
  record before the `has_hook` early-return)
- `agent/auxiliary_client.py` call wrapper (success + exception;
  records `caller='aux:<task>'`)

`api_request_id` is minted per call already — use it as id for dedupe.

### 3. Endpoint — `GET /api/analytics/models/performance?days=7&months=6`

Opens the requested profile's DB (same `_open_session_db_for_profile`
pattern). Returns:

```json
{ "period": {"days": 7, "months": 6},
  "models": [{
    "model": "claude-sonnet-4-6", "provider": "anthropic",
    "caller_roles": ["main"],
    "calls": 1286, "failures": 13, "success_rate": 0.990,
    "latency_ms": {"min": 700, "avg": 4100, "p95": 12800, "max": 43200},
    "tokens": {"input": 2100000, "output": 186000, "cache_read": 900000},
    "cost_usd": 3.42,
    "daily": [{"date": "2026-09-29", "calls": 120, "failures": 1,
               "avg_ms": 3900, "tokens": 310000}, ...]
  }],
  "monthly": [{"month": "2026-05", "model": "...", "calls": 412,
               "tokens": 810000, "avg_ms": null}, ...] }
```

**Backfill honesty:** monthly calls/tokens/cost come from `sessions`
(history exists for months). Latency + failures only exist from deploy
forward (`api_call_log` is new) — those series start empty and fill in;
`avg_ms: null` for months without log data. The 7-day latency/failure
panels show a "collecting — first day of data" empty state until calls
accrue, same convention as `CpuHistoryEmpty`.

### 4. agent-home

- `GET /api/models/performance` BFF route → `client.modelsPerformance()`.
  Lazy-fetch like `pinned` (additive; must not slow or break the page's
  config sections).
- New `ModelPerformance` section in `ModelsView.tsx`, placed after
  "Task roles" and before "In use":
  - Section header `Performance · last 7 days`.
  - One card per model: name + role pill(s) (reuse `slotTagFor`), stat
    grid (calls · success% with failure count, avg/p95, min/max, tokens,
    cost), 7-day per-day call bars (CapacityView-style).
  - `History · past 6 months` card: stacked per-month token bars by
    model + legend, with a `tokens | calls` segment toggle.
  - Failure counts get warn/red styling only when nonzero.

## Tests

- Backend: recorder writes on success + error + aux paths; aggregation
  SQL (empty → null-safe, month bucketing, provider folding reuse);
  endpoint shape; profile scoping.
- Frontend: card renders all stats, empty state, failure styling,
  history toggle; ModelsView integration test.
- Migration: fresh + legacy DB.

## Non-goals

- No outbound telemetry (AGENTS.md); strictly local reads.
- `web/` dashboard untouched — `agent-home` is the surface.
- Editing "In use" — it stays; Performance complements it (per-call
  latency/failures vs session/cost view). Can be merged later if
  redundant.
