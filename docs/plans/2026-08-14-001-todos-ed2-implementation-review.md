---
title: "review: To-dos ed.2 as implemented — what works, what is dead on arrival, and what the green suite hid"
status: resolved — fixes shipped in PR #235 (→ develop), PR #236 (develop → main), deployed to ECS
date: 2026-08-14
type: implementation review
target_repo: ai-prentice-4-all
reviews:
  - f8bda1702 (#226) feat(todos) ed.2 — CLI+skill, /start spawn, memory doc, nav badge, promotion seam
against:
  - docs/plans/2026-08-13-001-todos-and-projects-design-revision.md (ed.2 / ed.2a)
  - docs/plans/2026-08-11-001-todos-staging-layer-plan.md
baseline: develop @ 7e30a4424
resolved_by: 48973a102 (main, #236)
---

# To-dos ed.2 implementation review

Reviewed `develop` @ `7e30a4424`; the feature commit is `f8bda1702` ("feat(todos): ed.2 —
CLI+skill, /start spawn, memory doc, nav badge, promotion seam", merged as #226).

Verified locally: `ruff check` clean on the new modules, `tsc --noEmit` clean,
`pytest tests/hermes_cli/test_todos_cmd.py test_todos_ed2.py test_todos_promote.py` → 46 passed,
`vitest` on the three touched TS specs → 28 passed. The defects below are the ones the green
suite does not cover, most of them because the promotion tests mock `kanban_db.create_task`.

---

## Resolution summary

All items below were fixed in commit `f1481e952` (PR #235, merged to `develop`; PR #236
merged `develop` → `main` at `48973a102`, deployed to ECS on 2026-08-14). Item 3.1 (cron
cutover) was initially deferred as an architectural cleanup but completed in a focused
follow-up that cut the cron scheduler's duplicated AIAgent construction over to
`spawn_seeded_session`.

Each item heading below carries a **✅ Fixed** annotation with the commit that resolved it.

---

## 1. Blocking — shipped but non-functional

### 1.1 ✅ Fixed — Neither new button has a route behind it
`TodoDetailView` POSTs to `/api/todos/{id}/start` and `/api/todos/{id}/promote`, but
`agent-home/src/app/api/todos/[id]/` contains only `route.ts`, `stage/`, `complete/`, `snooze/`,
and there is no catch-all proxy. `lib/api/client.ts` has no `startTodo`/`promoteTodo` either.

Both requests 404 in the browser: "Work on this" shows *"Couldn't reach the AI layer."* and
"Promote to a project card" shows *"Promotion failed — try again."* The core endpoints exist and
are tested; only the BFF hop is missing, so the two user-visible affordances of this commit are
dead. Needs: two route handlers + two client methods, mirroring `stage/route.ts`.

### 1.2 ✅ Fixed — `POST /{id}/promote` fails 100% of the time
```python
card_id = create_task(kconn, ..., initial_status="triage", triage=True)
```
`kanban_db.VALID_INITIAL_STATUSES == {"running", "blocked"}`, so `create_task` raises
`ValueError: initial_status must be one of ['blocked', 'running']`. The `except Exception` around
it turns that into `HTTP 500 could not create the project card`. Reproduced directly:

```
>>> create_task(conn, title="promoted", priority="high", initial_status="triage", triage=True)
ValueError: initial_status must be one of ['blocked', 'running']
```

`triage=True` alone already forces `status='triage'` (documented in `create_task`'s docstring), so
the fix is to drop the `initial_status` argument.

### 1.3 ✅ Fixed — Promotion writes a string into an integer priority column
`_PROMOTE_PRIORITY_MAP` maps the to-do's four levels onto `"high"`/`"normal"`, but Kanban's
priority is `priority INTEGER DEFAULT 0` and `Task.priority: int`. With `initial_status` removed
the call succeeds and stores text:

```
stored: ('high', 'text', 'triage')
dataclass priority: 'high'
SELECT title, priority FROM tasks ORDER BY priority DESC
  → [('promoted', 'high'), ('int-5', 5)]
```

SQLite sorts text above every integer, so a promoted card jumps to the top of every board query
(`kanban_db` orders by `priority DESC, created_at ASC` in the dispatcher and the board view) and
`Task.priority`'s declared type is violated for that row. The map should produce ints.

`tests/hermes_cli/test_todos_promote.py` asserts `captured.get("priority") == "high"` against a
mocked `create_task` — the assertion is what let 1.2 and 1.3 through. AGENTS.md's "E2E validation,
not just green unit mocks" is the relevant rule; one real `create_task` against a temp kanban db
catches both.

### 1.4 ✅ Fixed — `hermes todos start --session` kills the session it starts
`_start()` launches `threading.Thread(target=_spawn, daemon=True)` and returns; `main()` then
exits and the interpreter tears down daemon threads. The CLI prints
`… -> working (session todo_…)` and the session never gets past import. For the CLI path the
thread should be joined (the CLI is the foreground; `/start` is the one that must detach).

### 1.5 ✅ Fixed — A cross-profile promote silently loses the project
`create_task` re-resolves `project_id` through `projects_db.connect_closing()` — still the
*per-profile* `$HERMES_HOME/projects.db`, because the shared-root migration (Projects plan step 1)
hasn't landed. A project that lives in another profile doesn't resolve, and `create_task`
deliberately drops the link rather than failing, so the endpoint returns 200 with a `project_id`
the card doesn't actually carry. Either gate promotion on the shared store landing, or verify the
returned card's `project_id` before reporting success.

## 2. Security / gating

All four items resolved in the same commit.

### 2.1 ✅ Fixed — `/start`'s `profile` check doesn't check anything
```python
_resolved = await _ps.get(profile)
if _resolved is None:  raise HTTPException(403, f"you do not hold profile {profile}")
_principal = _resolved
```
`PrincipalStore.get()` is a plain `SELECT … FROM principals WHERE user_id = $1` — it returns *any*
enrolled principal. The comment says "an explicit `profile` must be one the caller holds"; the code
only rejects a principal that doesn't exist. `_principal` is then used for
`_source_item(_principal, todo)`, so a caller can name another principal and have that principal's
arrival body read and injected into the seed prompt. `test_profile_not_held_is_refused` only
exercises the nonexistent-principal case, so it encodes the wrong contract.

Second half of the same problem: `profile_home` is `get_hermes_home()` regardless, so the resolved
profile has no effect on where the session actually runs. The parameter is both unsafe and inert —
simplest correct move is to drop it until FG-28 gives you a "profiles this subject holds" query,
then gate on that.

### 2.2 ✅ Fixed — `hermes todos send` is replayable
Nothing marks the approval consumed. A granted approval can be re-run any number of times and each
run delivers again — for the one irreversible surface in the feature, single-use is the property
that matters more than the routing match (which is implemented well). Record and check a `sent`
outbound event, or settle the notification, before delivering.

### 2.3 ✅ Fixed — `--account` is matched but never honoured
The routing check compares `--account` against the approval, then delivery calls
`send_message_tool({"target": f"{channel}:{target}[:thread]"})`, which has no account parameter.
C4's "the reply leaves by the account the message arrived on" — the reason `command_for` emits
`--account` at all — isn't enforced at the point of delivery on a multi-account channel.

### 2.4 ✅ Fixed — `promote` has no project authorisation
Any caller can promote into any project slug and set the link's `profile` to an arbitrary value
(`target_profile = body["profile"] or principal.user_id`). Expected, since the Projects permission
router doesn't exist yet — but it should be a stated precondition rather than an open door on a
shipped endpoint.

## 3. `agent/seeded_session.py`

All four items in this section are fixed.

### 3.1 ✅ Fixed — It is a second spawn path, not one path

The cron scheduler (`cron/scheduler.py` ~line 2444) previously constructed `AIAgent` directly
with its own config load, runtime resolution, credential pool, MCP discovery, SessionDB, and
~25-arg construction call — the same preamble `spawn_seeded_session` was extracted to share.

**Fix:** The cron scheduler now calls `spawn_seeded_session` for the agent construction + run,
passing pre-resolved values (`config`, `session_db`, `model`, `runtime`) so the helper doesn't
re-resolve them. Three new parameters were added to `spawn_seeded_session` for this cutover:
`config` (skip config.yaml reload), `session_db` (use caller's SessionDB, don't close it), and
`model` (use caller's resolved model). The `SeededSession` dataclass gained an `agent` field
so cron's `finally` block can call `agent.close()`. Dead variables (`fallback_model`,
`credential_pool`, MCP discovery block) were removed from the cron path — they now live
exclusively inside `spawn_seeded_session`. The module docstring — *"One session-spawn path
for cron and for to-do `/start`"* — is now true.

### 3.2 ✅ Fixed — The inactivity timeout never fires
```python
_tracker = getattr(agent, "_activity_tracker", None)
if _tracker is not None: ...
```
`AIAgent` has no `_activity_tracker`; cron reads
`agent.get_activity_summary()["seconds_since_activity"]` (`AIAgent._last_activity_ts`). `_tracker`
is always `None`, so the loop polls forever and a hung to-do session pins a thread in the web
server indefinitely — the failure mode the timeout was copied in to prevent.

Related, in the same block:
```python
if hasattr(agent, "_last_activity_ts"):
    _idle = concurrent.futures.thread._threads_queues  # noqa: SLF001
```
dead assignment poking a private stdlib symbol; delete it.

### 3.3 ✅ Fixed — A timed-out run isn't actually stopped
On timeout the helper calls `_future.cancel()` (a no-op once the future is running) and
`shutdown(wait=False)`. Cron additionally calls `agent.interrupt(...)`, which is what makes the run
stop. Without it, "timed out" only means the caller stopped waiting.

### 3.4 ✅ Fixed — Two smaller layering issues
- `os.environ["TERMINAL_CWD"] = workdir` mutates process-global env from a worker thread inside the
  web server; concurrent sessions with different workdirs will clobber each other.
- `agent/seeded_session.py` imports `cron.scheduler._expand_env_vars` — `agent` reaching into
  `cron` for a private helper inverts the dependency the extraction was meant to establish. Move
  the helper down (e.g. into `hermes_cli/managed_scope` or a config util) and have cron import it.

## 4. Smaller items — all fixed

- ✅ `--stage` help string — added `f` prefix.
- ✅ `/start` session pointer — added `TodoStore.record_session()` alongside `record_outbound()`,
  using `bind_principal` inside the transaction.
- ✅ `MobileShell` facets call — wrapped in `unstable_cache` (30s revalidation) so it is
  no longer a per-navigation round trip.
- ✅ `_memory_doc` — switched from raw `scope_filter` query to `bind_principal` inside the
  transaction.
- ✅ `send` exit codes — distinguished: `_SEND_DENIED=5`, `_SEND_ROUTING=6` (previously
  both returned `_SEND_PENDING=3`).
- ✅ `SKILL.md` — added `start` verb to the command list.
- `add --goal` — accepted but dropped (help says "not yet wired"); left as-is since the
  goals linkage is a separate feature gate.

- `--stage` help renders literally: `Comma-separated subset of {', '.join(TODO_STAGES)}` — missing
  `f` prefix (`todos_cmd.py:842`). Verified against `hermes todos list --help`.
- `/start`'s session pointer is written from the detached thread as raw SQL into `task_transitions`
  via `store._connect()`, with no `bind_principal` and wrapped in `except Exception: pass`. On an
  RLS-enabled tier a rejected insert is invisible. It deserves a `TodoStore.record_session()`
  alongside `record_outbound()`.
- `MobileShell` became `async` and now issues a `todosFacets()` call on **every** page render
  app-wide (Home, Chat, Inbox, Memory, Files, Settings, Graph…) to render one badge. Worth caching
  or scoping — it is a per-navigation round trip for a number most pages don't show.
- `_memory_doc` hardcodes `get_store("supabase-app", "prod")` and runs a `scope_filter` query
  without `bind_principal` (every other registry binds inside the transaction). If RLS is the
  backstop on that tier the query degrades to `None` silently and the row never renders.
- `send` returns exit 3 for pending, denied *and* routing mismatch, though the comment says the
  codes exist so a script can tell them apart.
- `skills/productivity/todos/SKILL.md`'s command list omits `start`, so the agent can't discover
  the verb; and `add --goal` is accepted then dropped (help says "not yet wired").

## 5. What matches the design

`--actor` throughout; `send` gated on answered+granted with the body taken from the approval row
and never argv, plus the routing-match refusal; the stage change committed before and
independently of the spawn; `/start` and promote offered only from `staged`/`open`; the badge as a
named slot on a server-safe `NavItem` array with the count passed as a prop; the `/files`
back-link using a `source_ref` filter the list API really supports; `project_links` as a
pointer-not-copy table with the owning profile on the row; promotion landing in `triage` and
moving the to-do to `working` rather than `done`; `/advance` correctly still absent.
