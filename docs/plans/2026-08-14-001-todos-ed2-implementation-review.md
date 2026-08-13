---
title: "review: To-dos ed.2 as implemented — what works, what is dead on arrival, and what the green suite hid"
status: partially resolved — 14 of 20 fixed; 6 open (see "Second pass"), one a regression
date: 2026-08-14
revised: 2026-08-14 (second pass — verified the #235/#238 fixes against develop @ 2e3b21ea5)
type: implementation review
target_repo: ai-prentice-4-all
reviews:
  - f8bda1702 (#226) feat(todos) ed.2 — CLI+skill, /start spawn, memory doc, nav badge, promotion seam
  - f1481e952 (#235) fix(todos) ed.2 review fixes
  - 13209c822 (#238) refactor(cron) spawn the job agent through spawn_seeded_session
against:
  - docs/plans/2026-08-13-001-todos-and-projects-design-revision.md (ed.2 / ed.2a)
  - docs/plans/2026-08-11-001-todos-staging-layer-plan.md
baseline: develop @ 7e30a4424
second_pass_baseline: develop @ 2e3b21ea5
resolved_by: 48973a102 (main, #236) — for the 14 items marked ✅ Fixed (verified)
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

Fixes were shipped in `f1481e952` (PR #235) and `13209c822` (PR #238). A **second pass**
re-verified every item against `develop` @ `2e3b21ea5`: **14 of 20 are genuinely fixed, 6 are
not.** Headings below are annotated **✅ Fixed (verified)** or **❌ Open (second pass)**.

---

## Second pass — the six still open

Read this section first if you are picking up the fix work. Each item names the file, why the
shipped fix does not do what its commit message claims, and what the fix has to be. Ordered by
severity.

All of §1.1–1.4, §2.1, §3.1–3.3 and six of the seven §4 items are confirmed fixed — 1.2/1.3
were re-reproduced against a real board (`priority` now stores as `integer`, status `triage`),
and cron genuinely calls `spawn_seeded_session` now.

### S1 (was 2.2) — `hermes todos send` is now broken for *every* send, not just replays

`TodoStore.list_outbound()` (`hermes_cli/todo_store.py`) runs:

```sql
SELECT to_state, actor, created_at FROM task_transitions
 WHERE task_id = $1 AND to_state LIKE 'action:%' ORDER BY created_at DESC
```

`task_transitions` has **no `created_at` column** — the timestamp is `ts`
(`hermes_cli/task_registry.py`: `id, task_id, from_state, to_state, ts, actor`). So every call
raises asyncpg `UndefinedColumnError`, which is not in `_run`'s
`except (TodoError, PermissionError, RuntimeError, ValueError)` — the CLI tracebacks. Because
the guard runs *before* delivery, `hermes todos send` cannot deliver at all: strictly worse than
the replay hole it was added to close.

Second, independent bug in the same guard — it could never fire even with the column fixed:

```python
if any(e.get("event") == "sent" for e in existing):   # todos_cmd.py _send()
```

`record_outbound()` writes `to_state = f"action:{event}:{channel}"`, i.e. `action:sent:whatsapp`.
The comparison is against the raw `to_state`, so it never equals `"sent"`.

**Fix:** select `ts` (alias it if callers want `at`), and match the event by parsing the
`action:<event>:<channel>` shape (e.g. `e["event"].split(":")[1] == "sent"`) or have
`list_outbound` return the parsed event rather than the raw `to_state`. Then add a test that
does **not** mock `list_outbound` — `tests/hermes_cli/test_todos_cmd.py` sets
`store.list_outbound = AsyncMock(return_value=[])`, which is exactly why both bugs shipped, in
the same way mocking `create_task` hid 1.2/1.3. A replay test must assert the second send is
refused, not just that the first succeeds.

### S2 (was 4, `_memory_doc`) — the "fix" widened access instead of narrowing it

`hermes_cli/todos_api._memory_doc()` replaced the `scope_filter` predicate **with**
`bind_principal`:

```python
await bind_principal(conn, principal)
row = await conn.fetchrow("SELECT id, title FROM rag_documents WHERE id = $1::uuid", doc_id)
```

`scope_filter` *is* the app-layer visibility filter (see `hermes_cli/access.py`'s module
docstring); `bind_principal` only sets GUCs, which are inert unless RLS policies exist on that
table. No `rag_documents` policy ships in this repo, and every other reader —
`memory_explorer.py`, `file_registry.py` — filters with `scope_filter`. The query now returns any
document by id regardless of visibility.

**Fix:** keep both — `bind_principal(conn, principal)` *and* the `scope_filter` predicate. The
original finding was "missing principal binding", not "replace the filter".

### S3 (was 4, `MobileShell`) — the badge cache is cross-principal and probably never runs

`agent-home/src/components/MobileShell.tsx`:

```ts
const getCachedFacets = unstable_cache(
  async () => (await apiClientForRequest()).todosFacets(),
  ["todos-facets-badge"], { revalidate: 30 });
```

Two problems. `apiClientForRequest()` → `readSession()` → `cookies()`, and Next 15 rejects
`cookies()` inside `unstable_cache`; the call site's `try/catch` swallows it, so the badge
silently stops rendering rather than erroring visibly. And the cache key contains no principal,
so if it does resolve, one user's open count is served to every other user of the box — the
exact cross-principal leak FG-28 exists to prevent.

**Fix:** read the session *outside* the cached function and pass the principal (or the token's
subject) into the key array, e.g.
`unstable_cache(fn, ["todos-facets-badge", principal.user_id], {revalidate: 30})` with the client
constructed from values already resolved by the caller. Verify with two sessions that the counts
differ; a unit test won't see this.

### S4 (was 1.5) — cross-profile promote is detected, not fixed, and now leaks a card

`create_task` still silently nulls a `project_id` that doesn't resolve through the *per-profile*
`projects_db.connect_closing()` (`hermes_cli/kanban_db.py`, "must not crash task creation or
persist a dangling reference"). The added check reads the card back and raises HTTP 500
`project did not resolve on the target board` — but the card has already been inserted, so a
failed promote now leaves an **orphan `triage` card** on the board with no project and no
`project_links` row, and the to-do is left wherever it was.

**Fix:** resolve the project *before* creating the card (call `projects_db.get_project` in the
same store `create_task` will consult, and 404/409 if it doesn't resolve), or delete the card in
the failure branch. The underlying cause remains the un-landed shared-root Projects store
(Projects plan step 1); until it lands, promotion is single-profile only and should say so.

### S5 (was 2.3) — `--account` is still not honoured, only passed

`todos_cmd._send()` now does `_send_args["account"] = account`, but
`tools/send_message_tool._handle_send()` reads only `args["target"]` and `args["message"]` — the
key is discarded. (The `account` at `send_message_tool.py:1311` is platform config from `extra`,
not a call argument.) C4's "the reply leaves by the account the message arrived on" is still
unenforced on a multi-account channel.

**Fix:** either thread an account through `_handle_send` → the platform adapter, or encode it in
the target the adapter already understands. If neither is cheap, make the gap explicit: refuse
the send when the approval carries an `--account` that delivery cannot honour, rather than
delivering by the wrong account.

### S6 (was 3.4, first half) — the `TERMINAL_CWD` fix isn't one

```python
_ctx = context or contextvars.copy_context()
_ctx.run(os.environ.__setitem__, "TERMINAL_CWD", workdir)   # agent/seeded_session.py
```

The comment says "thread-local context, not process-global env", but a `contextvars.Context`
does not isolate `os.environ` — this is the same process-global mutation as before, now harder
to spot. Two concurrent seeded sessions with different workdirs still clobber each other.

**Fix:** pass the workdir to the agent/terminal backend explicitly, or set it inside the worker
via a ContextVar the terminal tool reads. If a real env var is unavoidable, scope it with
`os.environ` save/restore around the `run_conversation` call and document that concurrent
seeded sessions serialise on it.

(The other half of 3.4 — the `_expand_env_vars` import — **is** fixed: it now comes from
`hermes_cli.config`, not `cron.scheduler`.)

### Second-pass verification performed

`develop` @ `2e3b21ea5`: `pytest test_todos_cmd.py test_todos_ed2.py test_todos_promote.py` → 46
passed; `ruff` clean on the five touched Python modules; `tsc --noEmit` clean; `vitest` on the
three TS specs → 28 passed. All six items above are invisible to that suite. A real
`create_task` against a temp board confirmed 1.2/1.3 fixed
(`stored: (2, 'integer', 'triage')`) and confirmed S4's silent `project_id` drop.

---

## 1. Blocking — shipped but non-functional

### 1.1 ✅ Fixed (verified) — Neither new button has a route behind it
`TodoDetailView` POSTs to `/api/todos/{id}/start` and `/api/todos/{id}/promote`, but
`agent-home/src/app/api/todos/[id]/` contains only `route.ts`, `stage/`, `complete/`, `snooze/`,
and there is no catch-all proxy. `lib/api/client.ts` has no `startTodo`/`promoteTodo` either.

Both requests 404 in the browser: "Work on this" shows *"Couldn't reach the AI layer."* and
"Promote to a project card" shows *"Promotion failed — try again."* The core endpoints exist and
are tested; only the BFF hop is missing, so the two user-visible affordances of this commit are
dead. Needs: two route handlers + two client methods, mirroring `stage/route.ts`.

### 1.2 ✅ Fixed (verified) — `POST /{id}/promote` fails 100% of the time
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

### 1.3 ✅ Fixed (verified) — Promotion writes a string into an integer priority column
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

### 1.4 ✅ Fixed (verified) — `hermes todos start --session` kills the session it starts
`_start()` launches `threading.Thread(target=_spawn, daemon=True)` and returns; `main()` then
exits and the interpreter tears down daemon threads. The CLI prints
`… -> working (session todo_…)` and the session never gets past import. For the CLI path the
thread should be joined (the CLI is the foreground; `/start` is the one that must detach).

### 1.5 ❌ Open (second pass — see S4) — A cross-profile promote silently loses the project
`create_task` re-resolves `project_id` through `projects_db.connect_closing()` — still the
*per-profile* `$HERMES_HOME/projects.db`, because the shared-root migration (Projects plan step 1)
hasn't landed. A project that lives in another profile doesn't resolve, and `create_task`
deliberately drops the link rather than failing, so the endpoint returns 200 with a `project_id`
the card doesn't actually carry. Either gate promotion on the shared store landing, or verify the
returned card's `project_id` before reporting success.

## 2. Security / gating

2.1 and 2.4 resolved; 2.2 and 2.3 are still open (S1, S5).

### 2.1 ✅ Fixed (verified) — `/start`'s `profile` check doesn't check anything
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

### 2.2 ❌ Open (second pass — see S1) — `hermes todos send` is replayable
Nothing marks the approval consumed. A granted approval can be re-run any number of times and each
run delivers again — for the one irreversible surface in the feature, single-use is the property
that matters more than the routing match (which is implemented well). Record and check a `sent`
outbound event, or settle the notification, before delivering.

### 2.3 ❌ Open (second pass — see S5) — `--account` is matched but never honoured
The routing check compares `--account` against the approval, then delivery calls
`send_message_tool({"target": f"{channel}:{target}[:thread]"})`, which has no account parameter.
C4's "the reply leaves by the account the message arrived on" — the reason `command_for` emits
`--account` at all — isn't enforced at the point of delivery on a multi-account channel.

### 2.4 ✅ Fixed (verified, as documentation) — `promote` has no project authorisation

Stated as a precondition in the endpoint docstring. Still unenforced — any caller can promote
into any project slug — so this closes the "undocumented open door" finding only; the gate lands
with the Projects permission router.
Any caller can promote into any project slug and set the link's `profile` to an arbitrary value
(`target_profile = body["profile"] or principal.user_id`). Expected, since the Projects permission
router doesn't exist yet — but it should be a stated precondition rather than an open door on a
shipped endpoint.

## 3. `agent/seeded_session.py`

3.1–3.3 fixed; 3.4's `TERMINAL_CWD` half is still open (S6).

### 3.1 ✅ Fixed (verified) — It is a second spawn path, not one path

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

### 3.2 ✅ Fixed (verified) — The inactivity timeout never fires
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

### 3.3 ✅ Fixed (verified) — A timed-out run isn't actually stopped
On timeout the helper calls `_future.cancel()` (a no-op once the future is running) and
`shutdown(wait=False)`. Cron additionally calls `agent.interrupt(...)`, which is what makes the run
stop. Without it, "timed out" only means the caller stopped waiting.

### 3.4 ⚠️ Half fixed (second pass — see S6) — Two smaller layering issues
- `os.environ["TERMINAL_CWD"] = workdir` mutates process-global env from a worker thread inside the
  web server; concurrent sessions with different workdirs will clobber each other.
- `agent/seeded_session.py` imports `cron.scheduler._expand_env_vars` — `agent` reaching into
  `cron` for a private helper inverts the dependency the extraction was meant to establish. Move
  the helper down (e.g. into `hermes_cli/managed_scope` or a config util) and have cron import it.

## 4. Smaller items — five fixed, two open

- ✅ `--stage` help string — added `f` prefix.
- ✅ `/start` session pointer — added `TodoStore.record_session()` alongside `record_outbound()`,
  using `bind_principal` inside the transaction.
- ❌ `MobileShell` facets call — wrapped in `unstable_cache`, but the cache is cross-principal
  and the cached function calls `cookies()`. **See S3.**
- ❌ `_memory_doc` — `scope_filter` was *replaced by* `bind_principal` rather than joined to it,
  which widens access. **See S2.**
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
