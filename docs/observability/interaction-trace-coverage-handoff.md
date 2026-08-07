# Interaction trace coverage — handoff

Scope: what a "trace" is, which surfaces write to the C8 ledger today, how the
agent-home + cron coverage added in PR #138 works, and what is still open.
Read alongside `docs/design/master-plan/feature-groups/FG-16-action-tracking-traceability.md`
(the contract) and `docs/observability/README.md` (the plugin observer hooks,
which are a *different*, plugin-facing contract).

## 1. What a trace is

A trace is **one end-to-end interaction**, identified by one `trace_id`. Each
step of that interaction is one append-only row in the `interactions` table
(contract C8, Supabase `app_*`, C3-routed, C2/RLS-scoped):

```
interactions(id, trace_id, parent_id|null, ts, actor_user_id, session_key,
             platform, kind, ref, summary, payload_ref|null, mode)

kind ∈ {inbound, turn, tool_call, tool_result, outbound,
        approval, change, cost, error, core_denied}
```

Lifecycle: `inbound → turn → tool_call → tool_result → outbound`, plus any
`approval`/`change`/`cost`/`error` the interaction causes. `parent_id` makes it
a reconstructable tree.

Hard invariants:

- **Observability-only.** Trace rows are never read back into a live turn and
  never injected into the system prompt or conversation. Prompt caching is
  sacred; the ledger is a side channel.
- **Fail-open.** Every trace call site swallows its own errors. An unconfigured
  or broken datastore must leave the turn/job untouched.
- **Access-scoped (C2).** Rows carry `actor_user_id`; RLS gives a member their
  own traces and the owner all of them.

A trace is **not** a session. Sessions/transcripts live in SessionDB
(`hermes_state.py`); the ledger references ids rather than duplicating content.

## 2. Where it surfaces

- Read API: `GET /api/comms/traces` (list) and `GET /api/comms/traces/{trace_id}`
  (detail) in `hermes_cli/web_server.py`. Both read the **prod** schema via
  `_comms_app_store()`.
- agent-home Activity tab: `agent-home/src/app/activity/page.tsx` →
  `agent-home/src/components/activity/TraceTimeline.tsx`.
- The card has **no title**. The accent pill is `trace.platform`, aggregated
  from the trace's rows by `InteractionLedger.list_traces()`. If a surface
  mints its trace with the wrong platform (or doesn't mint one at all),
  Activity shows exactly that.

## 3. Who mints traces (current state, post-#138)

| Surface | Entry point | `platform` | Actor |
|---|---|---|---|
| Telegram / WhatsApp / email / other channels | `gateway/run.py::_handle_message_with_agent` | inbound channel platform | `source.internal_user_id or source.user_id` |
| agent-home chat (streaming + non-streaming) | `hermes_cli/web_server.py` `/api/sessions/{id}/chat` and `/chat/stream` | `agent_home` | resolved C1 principal |
| cron / calendar-triggered runs | `cron/scheduler.py::run_job` | `cron` | enrolled owner |

All three call the same factory, `hermes_cli/interactions.py::create_trace()`
(renamed from `create_gateway_trace`; there is exactly one factory — do not add
a second).

Key detail — **schema mode**: the gateway passes a `SessionOrigin` and lets C3
derive the mode. Off-gateway surfaces have no inbound channel session, so they
pass `mode="prod"` explicitly. That is deliberate: the read APIs serve the prod
schema, so a trace written anywhere else is invisible in Activity.

Not traced today: CLI/TUI runs, webview runs, `hermes -q` one-shots, and any
other surface that neither passes the gateway chokepoint nor mints its own
trace. Adding one is the same 3 calls (mint → bind → flush).

## 4. How the added coverage works

Turn/tool spans need **no per-surface emitters**: `agent/conversation_loop.py`
and `model_tools.py` already call `observe()`/`observe_tool_call()`/
`observe_tool_result()`, which read the active trace from a **contextvar**
(`hermes_cli/interactions.py::bind_trace`). So a new surface only has to:

1. **mint** — `create_trace(...)` (returns `(trace, ledger)`, or `(None, None)`
   when `action_tracking.enabled` is false);
2. **bind** — `with bind_trace(trace):` *around the agent call, in the thread
   that runs the agent*;
3. **flush** — `await ledger.flush(trace)` once the interaction ends.

### Threading (the part that is easy to get wrong)

- `loop.run_in_executor(None, _run)` does **not** propagate contextvars. So in
  both agent-home endpoints the bind happens **inside** `_run()` (the executor
  thread), not on the request coroutine. Binding on the coroutine would leave
  every tool span untraced.
- The endpoint coroutine therefore has no bound trace, and emits its own
  `inbound`/`outbound`/`error` rows through `_trace_emit()`, which re-derives
  the parent via `trace.parent_for()` the way `observe()` would. Without that
  the reply would hang off the trace root instead of the turn it answers.
- Flush is awaited on the request loop (`_flush_agent_home_trace`), in the
  endpoint's `finally` for `/chat` and in `_driver()`'s `finally` for
  `/chat/stream`.
- Cron is the mirror image: `run_job` executes on a scheduler worker thread
  with **no running event loop**, so `_start_cron_trace()` binds via an
  explicitly entered context manager and `_finish_cron_trace()` flushes with
  `asyncio.run(...)` and then unbinds. The bind must stay before
  `contextvars.copy_context()` in `run_job`, which is how the trace reaches the
  inner agent worker thread.
- Cron has no sender, so the trace is attributed to the enrolled owner via
  `PrincipalStore.get_owner()` — one extra DB round trip per job, skipped when
  the app store has no DSN.

### Call sites

| File | Symbols |
|---|---|
| `hermes_cli/interactions.py` | `create_trace()`, `bind_trace()`, `observe*()`, `InteractionLedger.flush/list_traces` |
| `hermes_cli/web_server.py` | `_agent_home_trace()`, `_trace_emit()`, `_flush_agent_home_trace()` |
| `cron/scheduler.py` | `_start_cron_trace()`, `_cron_trace_event()`, `_finish_cron_trace()` |

### Tests

- `tests/hermes_cli/test_web_server_session_chat.py` — both endpoints: one
  owner-attributed `agent_home` trace, `inbound→turn→outbound` causation chain,
  flushed exactly once, trace bound in the agent thread, and a broken ledger
  does not fail the turn.
- `tests/cron/test_cron_interaction_trace.py` — owner-attributed `cron` trace,
  error rows, contextvar not leaked to the next job, unconfigured datastore
  leaves the job untraced.
- `tests/gateway/test_interaction_trace.py`, `tests/hermes_cli/test_interactions*.py`
  — pre-existing gateway/ledger coverage (includes the cache-safety test).

## 5. Verification status

Verified: unit/endpoint tests above, `ruff check` clean on touched files, `ty`
adds no new diagnostics.

**Not** verified: live end-to-end on the systest box. Nobody has confirmed real
rows landing in `app_prod.interactions` for `agent_home`/`cron` and rendering in
Activity. That is the top open item — see
`.agents/skills` / the `testing-hermes-systest-box` skill for the access path
(no SSH; Alibaba Cloud MCP `OOS_RunCommand`).

Suggested live check:

1. Send an agent-home chat turn → Activity shows a new card with an
   `agent_home` pill and an `inbound / turn / outbound` breakdown; open
   `/activity/{trace_id}` for the ordered tree.
2. Let a cron/calendar job fire → a `cron` card appears, owner-attributed.
3. Confirm a member cannot see another member's trace (RLS), and that prompt
   bytes are unchanged with tracing on vs off.

## 6. Open items / known gaps

- **Live systest validation** (above) — required before calling FG-16 coverage
  done.
- **Cron actor attribution.** Jobs carry no owner field, so every cron trace is
  attributed to the enrolled owner. If jobs ever record a creator, switch
  `_start_cron_trace()` to use it. (Flagged to Leo; awaiting a decision.)
- **Activity card labelling.** The card still shows only the platform pill and
  no session title. Now that platforms are accurate this is cosmetic, but a
  session title / humanized surface label would read better.
- **Untraced surfaces.** CLI, TUI, webview, one-shot runs (see §3).
- **Approval rows.** agent-home streaming has an approval surface
  (`register_gateway_notify`), but no `approval`-kind rows are emitted from it
  yet; the kind exists in the contract.

## 7. Local dev notes

- Tests run with the pre-provisioned venv at `~/.hermes/venvs/hermes-dev`
  (`python3` already resolves to it). It was missing `asyncpg` (a pyproject
  `dev` extra), which makes any test importing `hermes_cli/changes.py` error on
  collection: `uv pip install --python "$HOME/.hermes/venvs/hermes-dev/bin/python3" "asyncpg==0.31.0"`.
- Whole-directory runs of `tests/hermes_cli` show cross-test pollution failures
  that reproduce on a clean `develop` worktree; validate per-file.
  `tests/cron/test_cron_no_agent.py::test_run_job_no_agent_empty_output_is_silent`
  also fails on clean `develop`.
- Several agents work this repo concurrently: fetch and rebase onto
  `origin/develop` before starting and again before pushing.

## 8. History

- PR #138 — `feat(traceability): trace agent-home chat and cron runs (FG-16/C8)`,
  merged into `develop` (`6cbbd183f`). Audit-log row 3 in the FG-16 doc.
