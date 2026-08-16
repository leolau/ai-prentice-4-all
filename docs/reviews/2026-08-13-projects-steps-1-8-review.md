# Projects steps 1–8 — review findings

Audience: the agent implementing `docs/design/master-plan/feature-groups/FG-32-projects-durable-record.md`.
Every finding names the call site, what actually happens, the shipped seam to
use, and the test that would have caught it. Severities: **H/F-high** = the
designed behaviour does not happen; **M/med** = wrong in a reachable case;
**L/low** = correctness or hygiene. Nothing here has been fixed.

# Steps 1–5 (backend)

Reviewed at `bfbb2f4d2` (step 5) against `docs/design/master-plan/feature-groups/FG-32-projects-durable-record.md` (ed.3.2).
PRs: #251 (store), #252 (kanban_view), #254 (API), #258 (runs), #259 (schedule).
Targeted suite: 137 passed (`tests/hermes_cli/test_projects_*.py`, `test_kanban_view.py`).

Overall: the shapes are right. The store enforces the mandatory fields, the
progress ladder rungs are ordered as designed, board reads pass `principal`
everywhere (`projects_api.py:215`, `:707`, `kanban_view.py:189`), unauthorised
reads 404, contact addresses are dropped rather than blanked, and the schedule
module reuses `cron/jobs.py` in the host profile's store instead of writing a
scheduler. Findings below are concentrated in one place: **step 4's three
"upstream seam hasn't landed" hooks, all of which target APIs that do not exist
while the real shipped seams do.**

## H1 — Checkpoint/budget approvals are never raised (FG-10 has landed)

`projects_run.raise_approval()` (`hermes_cli/projects_run.py:528-545`) imports
`agent.human_comms` — no such module. Every call therefore takes the
`except Exception` path and logs at INFO. So on a `supervised` run with
checkpoints (`start_run`, `:709-716`) and on every budget stop (`budget_gate`,
`:584`) **no human is ever notified**; the run just sits in `waiting`.

The seam exists and is used by two shipped callers:
`hermes_cli.human_comms.NotificationStore.create(...)` (`human_comms.py:342`,
async, requires `target_user_id` + `title`), wired as in
`hermes_cli/todo_notifier.py:191` and `hermes_cli/todos_cmd.py:463`.

Severity: high — this is the gate the autonomy model rests on (§4), and it is
silently a no-op.

## H2 — `budget_usd_per_run` is unenforceable

Two independent reasons:

1. `run_cost()` (`:548-566`) imports `hermes_cli.interactions.sum_cost_for_trace`
   — does not exist. The shipped reader is
   `InteractionLedger.get_trace(trace_id, principal)` → `TraceSummary` (async;
   see `web_server.py:4192-4195`). So `run_cost` always returns `None` and
   `budget_gate` always returns `None` (`:577-579`).
2. Even with a correct reader there is nothing to read: `_trace_id_for()`
   (`:592-593`) mints a synthetic `proj-<uuid>` string that is never bound to a
   trace (`interactions.create_trace()` / `bind_trace()` are never called), and
   `_default_spawn_inline` does not propagate one. The id is decorative.

Fail-open on *observability* is right; fail-open on a *budget* is not — a
project with `autonomy=autonomous` and a budget set behaves as unbudgeted.
Recommend either wiring `create_trace`/`bind_trace` around the run and reading
the ledger, or (cheaper, honest) surfacing `cost: "not recorded"` and refusing
to advertise `budget_usd_per_run` in the API until C8 is wired.

## H3 — Toolset narrowing is computed against the *caller's* profile, not the host's

`_enabled_toolsets_for_profile(profile)` (`:827-839`) ignores its `profile`
argument entirely: it calls `load_config()`, i.e. the config of whatever
profile the web server / CLI process is running as. Consequences, both against
§4.1 ("intersection with the **host profile**", invariant 14 in §18):

- if the calling profile enables a toolset the host profile disables, the
  intersection lets it through → a **grant**, which the invariant forbids;
- if `config.yaml` has no `toolsets` key the function returns `[]`, so the
  intersection is empty and every requested toolset is silently reported as
  "dropped by the host profile".

Same class in `_available_skill_names()` (`:842-849`): `get_skill_commands()`
resolves skills for the *current* profile, and `projects_api.patch_tools_route`
(`:1721`) validates write-time skill names through it, so a project hosted on
another profile is validated against the wrong skill set.

Fix shape: resolve under the host profile's home, the way the schedule module
already does for cron (`projects_schedule._cron_in_profile`) and the way
`profiles.get_profile_dir(profile)` allows.

## H4 — An inline run does not execute in the host profile

`_default_spawn_inline` (`:874-902`) calls `spawn_seeded_session()` without
`profile_home`. The shipped callers pass it (`todos_api.py:341`,
`todos_cmd.py:414`), and `agent/seeded_session.py` uses it for
`profile_runtime_scope` — SessionDB, memory, skills, config. So a manual
`POST /{slug}/runs` from the dashboard runs the project's inline steps inside
the *server's* profile while `project_runs.profile` records the host profile:
the record lies, and profile isolation is crossed. (The *scheduled* path is
correct — the cron job lives in the host profile's store and shells out to
`hermes projects run`, so it inherits the right profile. The inconsistency
between the two trigger paths is the tell.)

Severity: high — profile isolation is a design invariant (§11), and this is the
one place Projects spawns a session.

## M1 — A repeatable project that has never run is never `stalled`

`derive_health` (`projects_schedule.py:423-428`) computes `last_start` from the
run rows and guards `if last_start and ...`. With zero runs `last_start == 0`,
so the staleness branch is skipped and health is `ok` — exactly the silence
§15 failure mode 1 exists to name (schedule wired, cron never fired, nothing
ever produced). Anchor on `max(last_start, schedule set/created_at)` instead.
`doctor_findings` has `stalled_repeatable`, so the doctor and the health field
can currently disagree.

## M2 — List pagination can repeat rows when `?health=` filters

`_list_sync` (`projects_api.py:462-478`) skips non-matching rows, then derives
the cursor as `page[len(out) - 1]`. Once anything is filtered out, `len(out)`
no longer indexes the last row *examined*, so the next page restarts behind the
watermark (duplicates; with `limit`-sized filtering, a loop). The cursor should
come from the last examined row, independent of how many survived the filter.

## M3 — The project owner cannot see contact addresses

`projects_api.py:750` sets `include_address = role not in (None, "viewer")`, but
an instance admin or the project owner (`owner_user_id`) normally has **no**
`project_members` row, so `role is None` and the address is dropped — while
`_can_member_act` lets that same caller *create* the contact
(`_can_write` → owner/admin, `:134-147`). Condition should mirror
`_can_member_act`, not the raw role.

## L1 — Toolsets/skills are stored as CSV, design says JSON list

`patch_tools_route` joins with `","` (`:1716`, `:1730`) and
`parse_csv_field` splits on it; §1.1 rows 13/14 specify a JSON list. Harmless
until a skill name contains a comma, at which point it silently splits into two
unknown names. Either normalise the design or reject `,` in names.

## L2 — Legacy imported projects violate the mandatory-field invariants

`_import_one_profile_store` (`projects_db.py:2303-2380`) inserts with no
`goal`, no outputs, no members and no `owner_user_id` — correct for a migration,
but those rows then have `goal = NULL` (mandatory field [1]) and can never be
activated or scheduled until a human fills them in, and with `owner_user_id`
NULL and no member rows, `_can_write` grants nobody but an instance admin. Worth
an explicit "grandfathered/needs completion" state in the payload and a doctor
finding, rather than letting the UI show a project with an empty goal.

## Test gaps

Notes below are about the *design's* §16 matrix, not the passing suite:

- No test asserts the approval hook actually reaches a notification store
  (H1 passes today precisely because the assertion is absent).
- No test pins `enabled_toolsets` resolution to the **host** profile — the
  existing tests inject `enabled` directly, which is why H3 is invisible.
- No test asserts `spawn_seeded_session` is called with the host profile's
  `profile_home` (H4).
- No health test for a scheduled project with zero runs (M1).
- No pagination test combining `?health=` with a cursor (M2).
- No cross-profile negative test on `GET /{slug}/board` (the `principal`
  argument is passed, but nothing proves a foreign card is filtered out end to
  end).

---

## Fix recipes — steps 1–5

Each recipe names the exact call site, the shipped seam to use (verified in this
tree), and the test that would have caught it. Line numbers are at `93c5540eb`.

### H1 fix — route approvals through `NotificationStore`

`hermes_cli/projects_run.py:528-545`. Replace the `agent.human_comms` import
with the shipped store and await it; the verified signature is
`human_comms.py:342-356`:

```python
async def raise_approval(*, project, run_no, kind, title, body, reversible=True):
    from hermes_cli.human_comms import NotificationStore  # shipped seam
    store = NotificationStore(...)              # as in todo_notifier.py:191
    return await store.create(
        kind=kind,                              # NotificationKind
        target_user_id=project.owner_user_id,   # the human who owns the project
        title=title,
        body=body,
        reversible=reversible,                  # False for irreversible → never auto-approved
        dedupe_key=f"proj:{project.slug}:run:{run_no}:{kind}",
    )
```

Callers are sync today (`start_run` `:709-716`, `budget_gate` `:584`), so either
make the two callers async or hand off exactly the way `todos_cmd.py:463` does.
Keep the `except Exception` as a last resort but log at ERROR, not INFO — a
silent INFO is why this shipped. `reversible=False` matters: C6 never
auto-answers an irreversible approval, which is the design's §4 promise.

Test: assert `create` was called (a fake store recording kwargs) for a
`supervised` run with a checkpoint, and for a budget stop.

### H2 fix — either bind a real trace or stop advertising a budget

`run_cost()` `:548-566` and `_trace_id_for()` `:592-593`. The shipped reader is
`InteractionLedger.get_trace(trace_id, principal)` → `(interactions, TraceSummary)`
(`interactions.py:740`, async; caller example `web_server.py:4192-4195`), and a
trace only exists if minted: `interactions.create_trace(config=…, actor_user_id=…,
session_key=…, platform=…, mode="prod")` → `(trace, ledger)` (`:456-476`, note
the docstring naming off-gateway surfaces like cron), then
`with interactions.bind_trace(trace):` (`:380`) around the spawn so the run's
tool calls land under it.

Minimum viable fix:

1. In `start_run`, mint the trace with `mode="prod"`, store `trace.trace_id`
   on the run row instead of `proj-<uuid>`, and `bind_trace` around
   `_default_spawn_inline`.
2. In `run_cost`, `await ledger.get_trace(trace_id, principal)` and read the
   summary's cost.

If C8 wiring is out of scope for this step, do the honest alternative instead:
have `budget_gate` refuse to *silently* pass — either reject
`budget_usd_per_run` at the API with "not enforceable yet", or return
`cost_recorded=False` **and** surface a visible "budget not enforced" flag on
the project. Today an `autonomous` project with a budget behaves as unbudgeted,
and nothing tells the owner.

Test: a run whose ledger reports cost over budget must reach `waiting` with a
budget approval raised; a run with tracing disabled must report
`cost_recorded=False`.

### H3 fix — resolve toolsets/skills inside the host profile's runtime scope

`_enabled_toolsets_for_profile(profile)` `:827-839` and `_available_skill_names()`.
The tree already has the profile-scoping seam used by
`projects_schedule._cron_in_profile` (`projects_schedule.py:67-77`):

```python
def _enabled_toolsets_for_profile(profile: str) -> List[str]:
    from agent.profile_runtime import profile_runtime_scope
    from hermes_cli import profiles
    from hermes_cli.config import load_config_readonly
    try:
        with profile_runtime_scope(profiles.get_profile_dir(profile)):
            cfg = load_config_readonly() or {}
    except Exception:
        return []          # unknown profile → grant nothing (fail closed)
    ts = cfg.get("toolsets")
    return [str(t) for t in ts] if isinstance(ts, list) else []
```

Fail **closed**: today `except: return []` combined with the caller's
intersection semantics needs checking — confirm an empty list means "no project
narrowing beyond the host's own set", not "everything allowed". Same scope wrap
for `_available_skill_names()`.

Test: create profile A with `toolsets: [shell]` and host profile B with
`toolsets: []`; run as A on a B-hosted project and assert `shell` is **not** in
the effective set (invariant 14).

### H4 fix — pass the host profile's home to the spawn

`_default_spawn_inline` `:875-907` calls `spawn_seeded_session(...)` with no
`profile_home`, while the parameter exists (`agent/seeded_session.py:62-67`) and
the todo path passes it (`todos_cmd.py:414`). Fix:

```python
from hermes_cli import profiles
result = spawn_seeded_session(
    prompt,
    origin=f"projects:{project.slug}:run-{run.get('run_no')}",
    session_id=session_id,
    profile_home=str(profiles.get_profile_dir(host_profile)),
    enabled_toolsets=list(enabled_toolsets) if enabled_toolsets else None,
    context=contextvars.copy_context(),
)
```

`host_profile` is already resolved for the run row — thread it into this helper
rather than re-deriving it. Without this the session reads the *server's*
memory, secrets and soul while the run row claims the host profile.

Test: monkeypatch `spawn_seeded_session` and assert `profile_home` equals the
host profile's dir for both the inline and the cron entry point.

### M1 fix — a scheduled project that has never run is stalled

`projects_schedule.derive_health()`: `last_start` is `0` with no runs, so the
`if last_start and …` guard skips. Treat "never ran" as measured from the
schedule's creation (or the project's `activated_at`):

```python
anchor = last_start or int(project.get("activated_at") or 0)
if anchor and anchor < now - 2 * period:
    return "stalled"
```

Test: repeatable project, schedule set two periods ago, zero runs → `stalled`.

### M2 fix — take the cursor from the last row *examined*, not the last row kept

`projects_api._list_sync()` derives the next cursor from the emitted rows after
the `health` filter drops some, so the cursor can point behind rows already
examined and the next page repeats them. Keep a separate
`last_examined_key` updated for **every** row read from SQLite and emit that as
the cursor (or push the health predicate into SQL so the filter and the keyset
agree).

Test: 5 projects, filter `health=attention` matching only #1 and #5, `limit=2`
→ page 2 must not repeat #1.

### M3 fix — write authority implies address visibility

`projects_api.py:750`: `include_address = role not in (None, "viewer")` hides
addresses from an owner/instance-admin who has no explicit `project_members`
row (`role is None`). Derive from the same predicate that authorises writes:

```python
include_address = _can_write(...) or role in ("lead", "editor")
```

Test: project owner with no member row → contact address present; explicit
`viewer` → absent (not blanked, absent).

### L1 fix — reject `,` in toolset/skill names, or store JSON

`patch_tools_route` `:1716`, `:1730` joins with `","` and `parse_csv_field`
splits on it, while §1.1 rows 13/14 specify JSON lists. Cheapest correct fix:
validate names against `^[A-Za-z0-9_.:-]+$` on write and keep CSV. Note the UI
already assumes CSV (`ToolsPanel.tsx` `splitCsv`), so if you switch to JSON,
change both sides in one PR.

### L2 fix — mark imported legacy projects incomplete

`projects_db._import_one_profile_store()` `:2303-2380` inserts rows with no
`goal`, outputs, members or `owner_user_id`. Add `status='needs_completion'`
(or a `legacy_incomplete` flag), refuse activation/scheduling for such rows with
a message naming the missing fields, and emit a doctor finding per row so the
list page can show "needs completion" instead of an empty goal.

---

# Steps 6–8 (agent-home)

Reviewed at `93c5540eb` (step 8) against the same design (ed.3.2).
PRs: #261 (BFF routes, client, types), #263 (list page, Home card, nav),
#266 (detail page, run + card routes, `AddToProjectSheet`).

Checks run: `npm ci` in `agent-home` (deps were **not** installed on this box),
then `npx vitest run` → **437 passed, 3 failed**, and `npm run typecheck` →
clean. The three failures are `src/components/MobileShell.test.tsx`
(`renderToStaticMarkup` suspends under React 19); they fail **identically at
`bfbb2f4d2` (step 5)**, so they are pre-existing and not a steps 6–8
regression — do not spend fix time there.

What is right, so nobody "fixes" it: `force-dynamic` + `requirePrincipal()` on
all three pages; the detail fan-out is `| null` per resource so one failed read
degrades one panel (`[slug]/page.tsx:44-52`); Accept exists only in
`OutputsPanel`; Guidance says *applies from next run*; panel order matches §13;
`filters.ts` / `format.ts` are deliberately server-safe; slugs and ids are
`encodeURIComponent`d at every call site; `run.cost == null` renders
`not recorded` rather than `$0.00`.

The findings cluster in one place: **three write routes return an
acknowledgement envelope and the UI merges it as if it were the row.**

## Fix order

| # | Severity | Where | One-line fix |
|---|---|---|---|
| F1a | high | `OutputsPanel.tsx:47-53` + `projects_api.py:969-973` | return the output row; render `offers_closure` |
| F1b | high | `RunView.tsx:79-82` + `projects_run.py:783` | unwrap `{run, promoted, budget_gate}`; show the gate |
| F1c | high | `GuidancePanel.tsx:47-48` + `projects_api.py:1471` | return the directive row |
| F4 | med | `RunView.tsx:58-90` | `router.refresh()` on success (subsumes F1b) |
| F2 | med | `filters.ts:77` + `projects_api.py:462` | Attention must include `stalled` |
| F3 | med | `projects_api.py:482,511` | `@router.get("")` / `post("")` — kill the 307 |
| F5 | med | `ProjectDetailView.tsx:83` + `projects_api.py:330` | find the waiting run outside the 5-row brief |
| F6 | low | `hermes-bridge.ts` + `client.ts:189-193` | forward `err.body.detail`, not `err.message` |
| F7 | low | the three `page.tsx` files | `notFound()` on 404 |
| F8 | — | `cards/route.ts:24` + `projects_api.py:1284-1290` | step 8b is not on `develop`; `from_todo` is dropped |

## F1 — Three write endpoints: the UI merges a shape the API never returns

One bug class, three places, each the *only* surface for that judgement. All
three are invisible to the suite because `client.projects.test.ts` asserts URL,
method and body — never a response.

### F1a — Accepting an output does not change the row

`agent-home/src/components/projects/panels/OutputsPanel.tsx:38-58`:

```ts
if (!res.ok) throw new Error("accept");
// The accept route returns the bare row; keep the joined deliveries.  ← it does not
const updated = (await res.json()) as Record<string, unknown>;
setOutputs((prev) => prev.map((row) => (row.id === outputId ? { ...row, ...updated } : row)));
```

`accept_output` (`hermes_cli/projects_api.py:969-973`) returns:

```python
return {"accepted": output_id, "by": principal.user_id, "offers_closure": offers_closure}
```

Effect: no `status` key is merged, so the row stays `delivered`; the Accept
button is rendered on exactly that condition (`OutputsPanel.tsx:127`), so it
stays too, and the user reads a successful accept as a failure. `offers_closure`
— the design's §6.1 *this one-off can now close* offer — is dropped with nowhere
else in the UI to appear.

Fix (pick the API side; it is one line and it also fixes the CLI):

```python
# projects_api.py accept_output
row = projects_db.get_output(conn, project.id, output_id)   # after the write
return {**_output_payload(row), "offers_closure": offers_closure}
```

and in the panel, stop asserting `Record<string, unknown>` — type it
`ProjectOutput & { offers_closure?: boolean }`, and when `offers_closure` is
true, show the close affordance (or at minimum a "this project can be closed"
note) instead of discarding it.

Tests to add: `OutputsPanel` with a stubbed `fetch` returning the *real* payload
→ the row reads `accepted` and the Accept button is gone; a client test pinning
the accept response shape.

### F1b — Continuing a waiting run changes nothing on screen

`RunView.tsx:79-82`:

```ts
if (mergeUpdatedRun) {
  const updated = data as Partial<ProjectRun>;
  if (updated.status) setRun({ ...run, ...updated });   // never true for continue
}
```

`continue_run` returns an **envelope** (`hermes_cli/projects_run.py:783`, `:786`):
`{"run": {...}, "promoted": [...], "budget_gate": ...}` — `status` sits one
level down, so nothing merges and the page still shows `waiting` with a
Continue button. `cancel_run` (`:820`) returns a bare row, which is why Cancel
works: **the two routes disagree with each other.** `budget_gate`, i.e. the
reason a run is being held, has no rendering anywhere in the UI.

Fix, both sides:

```ts
const payload = data as { run?: ProjectRun; budget_gate?: string | null } & Partial<ProjectRun>;
const updated = payload.run ?? payload;
if (updated.status) setRun({ ...run, ...updated });
if (payload.budget_gate) setNotice(`Held: ${payload.budget_gate}`);
```

and make `continue_run` and `cancel_run` return the same envelope shape
(`{run, promoted, budget_gate}` for both) so no caller has to guess. With F4
applied, the merge stops being load-bearing — but the envelope mismatch should
still be fixed, because the CLI (step 10) will hit it next.

Tests: a `RunView` test per verb with the real payload, asserting the status
transition renders and a `budget_gate` is surfaced.

### F1c — A new directive renders blank

`GuidancePanel.tsx:32-55`:

```ts
const created = (await res.json()) as ProjectDirective;   // it is not one
setDirectives((prev) => [created, ...prev]);
```

`add_directive_route` (`projects_api.py:1471`) returns
`{"id": did, "applies_from": "next run"}`. The prepended row therefore has no
`body`, no `author`, no `created_at`, no `kind`, and renders as
`undefined · never` until a reload. Fix: return the full directive row from the
route (the GET already serialises one — reuse that serialiser), keeping
`applies_from` alongside it so the panel's "applies from next run" copy still
has its source. Test: stub `fetch` with the real payload and assert the new row
shows its body and author.

## F2 — The Attention chip cannot show a stalled project

`agent-home/src/components/projects/filters.ts:77` maps the chip to
`health=attention`, and the API filters by exact equality
(`projects_api.py:462`). `stalled` is the *worse* rung of §9.2, so a stalled
project appears only under Active and All — never under the view named for
needing a human. Combined with M1 (a repeatable project that never ran stays
`ok`) the list has no view that reliably surfaces breakage.

Fix: make the health filter accept a set — `health=attention` meaning
`attention OR stalled` (server-side, so the cursor and the filter agree, cf.
M2), or send `health=attention,stalled` and split on the API. Test: a `stalled`
project must appear under the Attention chip.

## F3 — Collection routes are one redirect off the client's URLs

`@router.get("/")` and `@router.post("/")` (`projects_api.py:482`, `:511`)
versus `/api/registry/projects` with no trailing slash in the client
(`client.ts:1150`, `:1161`). Verified against the real router with
`TestClient(follow_redirects=False)`: both are **307**. `fetch` follows it, so
this is not broken today — the costs are a second round trip on every list and
create, and a `Location` built from the `Host` header, which behind a proxy can
point at a different origin while undici replays the session cookie and bearer
token to it. The shipped convention one module over is `@router.get("")` /
`@router.post("")` (`todos_api.py:101`, `:152`). Fix the routes, not the client.
Test: `follow_redirects=False` on both collection routes asserts 200/201.

## F4 — `RunView` never revalidates after its own writes

`ProjectDetailView.post()` calls `router.refresh()` on success
(`ProjectDetailView.tsx:110`); `RunView.post()` (`RunView.tsx:58-90`) does not.
So after Retro, Continue or Cancel, `cost`, `duration_seconds`, `outcome`, the
promoted cards and the deliveries list all keep their server-rendered values —
the run page silently disagrees with the run. Fix: `router.refresh()` in
`RunView.post` after a 2xx, exactly as the detail view does. Test: assert
`router.refresh` is called after each verb.

## F5 — "Continue run N" in the header only exists for the last 5 runs

`ProjectDetailView.tsx:83` searches for the waiting run inside `project.runs`,
which is `_runs_brief(limit=5)` (`projects_api.py:330`). A supervised project
that keeps starting runs loses its resume affordance from the header once five
newer runs exist, even though the older run is still `waiting` — the run is
still reachable by URL, so this is a discoverability defect, not data loss.
Fix: have the detail payload carry `waiting_run_no` explicitly (cheap, one
query), or never truncate non-terminal runs out of the brief. Test: a project
whose only waiting run is the 6th-newest still offers Continue.

## F6 — Upstream error copy is replaced by the internal path

`agent-home/src/app/api/projects/hermes-bridge.ts` maps
`{ error: "api_error", detail: err.message }`, and `err.message` is
`"Hermes API /api/registry/projects/x/directives → 409"`; the real payload sits
in `err.body`, which is discarded (`client.ts:189-193`). Both consumers render
`data.detail` straight to the user (`ProjectDetailView.tsx:107`,
`RunView.tsx:76`), so the design's own copy — the 409 *retire one first* cap,
the budget refusal — never reaches anyone, and the API topology does. The todos
routes share this convention, so treat it as a convention fix:

```ts
const detail =
  err.body && typeof (err.body as { detail?: unknown }).detail === "string"
    ? (err.body as { detail: string }).detail
    : "That didn't go through.";
return NextResponse.json({ error: "api_error", detail }, { status: err.status });
```

Test: a BFF route test asserting a 409 with `{detail: "…"}` upstream reaches the
client verbatim and does **not** contain `/api/registry/`.

## F7 — A 404 renders as a load error, with the raw message

`[slug]/page.tsx:56-68`, `[slug]/runs/[runNo]/page.tsx:45-56` and
`[slug]/cards/[id]/page.tsx:94-105` all catch every failure into
`Couldn't load this … (${err.message})`. For the 404 the API deliberately
returns on an unreadable or missing project (§11) the right answer is Next's
`notFound()`; today the panel is indistinguishable from a real outage and
prints the upstream path. Fix: branch on `err.status === 404` → `notFound()`,
keep the panel (with generic copy, no `err.message`) for everything else. The
`runNo` guard at `runs/[runNo]/page.tsx:22-33` is good — keep it.

## F8 — Step 8b is not on `develop`

Latest Projects commit is `93c5540eb` (step 8), so the step-8b seams the code
comments promise are genuinely outstanding:

- `agent-home/src/app/api/projects/[slug]/cards/route.ts:24` forwards
  `from_todo`, but `create_card` (`projects_api.py:1284-1290`) never reads it —
  a promoted to-do's provenance is silently dropped, so the design's
  to-do → card link is unrecorded. Either persist it or reject the field.
- `AddToProjectSheet.tsx:474-475` tells the user "Promoting turns it into a card
  on the project's board" while the sheet offers no promote control; it only
  creates links.

Confirm with the implementing agent that 8b is still on their list — the review
found no promote path, no score entry (9b) and no conversation-histories panel
on `develop`.

## Test gaps (steps 6–8)

- **Zero tests under `agent-home/src/app/api/projects/**`** — for ~30 routes
  nothing covers the 401 path, upstream status pass-through, the 502 mapping,
  or a single route's body validation (`route.ts` mandatory-field checks, the
  empty-PATCH rejection).
- `client.projects.test.ts` (8 tests) asserts URL builders only. One test per
  mutation pinning **the shape the Python route actually returns** would have
  caught all three F1 defects; that is the single highest-value test to add.
- No test drives `OutputsPanel.accept`, `RunView.post` or `GuidancePanel.add`
  against a stubbed `fetch` — every interactive path in step 8 is uncovered.
  `ProjectDetailView.test.tsx` covers rendering well (panel order, degraded
  board, samples vs references, CSV tool chips) and nothing else.
- No test for the health chip against a `stalled` project (F2).
- No test that the list page or `AddToProjectSheet`'s project list omits a
  project the principal cannot read (the isolation is upstream, but nothing
  pins it end to end).
- No `follow_redirects=False` assertion on the collection routes (F3).
