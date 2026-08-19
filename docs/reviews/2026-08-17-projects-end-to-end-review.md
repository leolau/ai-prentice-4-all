# Projects — end-to-end review (steps 1–11, the whole shipped path)

Audience: the agent that owns Projects (FG-32).
Reviewed at `origin/develop` @ `7c737474f` ("Merge pull request #280 from
leolau/feat/projects-live") against
`docs/design/master-plan/feature-groups/FG-32-projects-durable-record.md`
(ed.3.2, formerly `docs/design/projects-feature-design.md`).

The steps 1–8 review — [`2026-08-13-projects-steps-1-8-review.md`](./2026-08-13-projects-steps-1-8-review.md),
merged as #270 — carries the **fix recipes** for findings H1–H4, M1–M3, L1–L2
and F1–F8. This pass does three things it did not: it re-verifies each of those
findings against the current tree, reviews what landed after it (step 8b, and
steps 9/9b/10/11 plus the `feat/projects-live` merge), and states where the
feature stands as a whole.

## What the code does well

The shapes held all the way up. Steps 9–11 did not bend the store or the
router: every new route goes through the same `_require_read` / `_require_write`
gate, every board read still passes `principal` (so another user's `private:`
card stays invisible through `/board`, `/events` and the card routes alike),
and the derived values are still computed on read — re-scoring run 3 moves the
project score with no write anywhere else.

Three later pieces are better than the design asked for:

- **The learning path is inactive-by-default in the store, not in the UI.**
  Every retro proposal materialises with `active=0` — a playbook revision
  carrying `note='proposed by run N'`, a directive row, or a skill candidate
  holding only project + run provenance (`projects_api.py:2011-2065`). The
  skill destination deliberately adds no mechanism and leaves the shipped
  `background_review` loop owning it, which is the right call per the footprint
  ladder. Step 10 also fixed a real pre-existing bug it tripped over —
  `save_playbook_rev` was silently dropping `note` because the column was
  missing from the `INSERT`.
- **`latest_event_id` is independent of the cursor and of the cap**
  (`kanban_db.py:3212-3243`), so a poller whose window overflowed still learns
  where the head is. That is the detail this kind of endpoint usually gets
  wrong.
- **The retro/score split is honest.** `score_self` rides the retro route,
  `score_user` is its own route, and the runs brief carries `score_self` only
  when it diverges from `score_user` by ≥2 (`projects_api.py:352-399`) —
  presence *is* the signal, exactly as §8.2 rule 2 asks.

Verification run at this revision: **185 passed** across the 15 Projects Python
suites; **46 passed** across the agent-home Projects component/client tests;
`tsc --noEmit` clean. The 3 `MobileShell.test.tsx` failures noted in the
previous review still reproduce and still pre-date this stack.

## Status of the previous 17 findings — 16 still open

Nothing from #270 has been fixed. Steps 9–11 were new work, not repair work.

| # | finding | status at `7c737474f` | evidence |
|---|---|---|---|
| H1 | checkpoint/budget approvals never raised (`from agent import human_comms` — module does not exist; the shipped seam is `hermes_cli.human_comms.NotificationStore.create`) | **FIXED** — Block 2: approvals raise through `NotificationStore.create` (irreversible, owner-targeted, deduped per run); the fail-open `except` is gone — a broken surface fails the run | `projects_run.py:528-546` |
| H2 | budget unenforceable — `sum_cost_for_trace` does not exist and `trace_id` is a synthetic string never bound to a trace | **FIXED** — Block 2: `start_run` mints a real C8 trace (`interactions.create_trace`, mode `prod`), binds it around the spawn and flushes it; cost reads through `InteractionLedger.get_trace` (sum of `kind='cost'` events); tracing off → no trace id, gate stays open | `projects_run.py:548-566`, `592-593`, `641` |
| H3 | `_enabled_toolsets_for_profile(profile)` ignores its argument and reads the *calling* process's config, so a project can be granted a toolset the host profile disables (invariant 14) | **FIXED** — Block 2: toolsets and skills resolve inside `profile_runtime_scope` of the named profile; unknown profile fails closed (no grant) | `projects_run.py:827-841` |
| H4 | inline run spawns without `profile_home`, so a manual run executes in the server's profile while the run row records the host profile | **FIXED** — Block 2: the default spawn passes `profile_home` (the host profile's dir) and `copy_context()` to `spawn_seeded_session` | `projects_run.py:869-894` |
| M1 | a repeatable project that has *never* run is never `stalled` (`if last_start and …`) | **FIXED** — Block 3: the never-fired anchor chain is `last_start` → the cron job's own `created_at` (ISO-parsed) → `project.created_at`; older than two periods → `stalled` | `projects_schedule.py` `derive_health` + `_epoch_from_timestamp` |
| M2 | health/read filtering happens **after** the page slice, and `next_cursor` is taken from the last *emitted* row | **FIXED** — Block 3: `_list_sync` is a single newest-first pass that filters before it appends and stops only once the page is full; the cursor is the last *examined* row, so an all-filtered page still hands back a cursor and no row is ever skipped or repeated | `projects_api.py` `_list_sync` |
| M3 | an instance owner/admin who is not a project member has `role is None`, so contact addresses are dropped from them too | **FIXED** — Block 3: `include_address` follows write authority (`_can_write` or role lead/editor), not membership alone | `projects_api.py` `get_project_detail` |
| L1 | `toolsets`/`skills` stored as CSV strings | **FIXED** — Block 4: CSV kept (the UI's `ToolsPanel` already splits CSV); names are validated against `^[A-Za-z0-9_.:-]+$` at write time in `patch_tools_route`, before the unknown-name check — a separator can no longer round-trip as two unknown names | `projects_run.py:646-652` |
| L2 | profile-imported legacy projects land with NULL `goal`, no outputs and no host profile — the mandatory-field invariant does not hold for them | **FIXED** — Block 4: quarantined — imports land with `status='needs_completion'`; leaving the status or scheduling is refused until a goal, an output and a host profile exist, each refusal naming what is missing; doctor emits a `needs_completion` finding and the list row renders it instead of an empty goal | `projects_db.py:2496-2517` |
| F1a | accept-output returns an ack envelope; `OutputsPanel` merges it as a row, so the row stays `delivered`, the button stays, and `offers_closure` is never surfaced | **FIXED** — Block 1: the route returns the updated row + the offer; the panel merges and surfaces a closure notice | `projects_api.py:1021-1025` vs `OutputsPanel.tsx:38-59` |
| F1b | continue-run returns `{run, promoted, budget_gate}`; `RunView` reads `data.status`, which never exists at the envelope level, so continuing appears to do nothing and the `budget_gate` holding the run is never shown | **FIXED** — Block 1: `RunView` unwraps `data.run` (or the bare row on cancel) and renders `budget_gate` | `projects_run.py:786` vs `RunView.tsx:84-86` |
| F1c | add-directive returns `{id, applies_from}`; `GuidancePanel` prepends it as a `ProjectDirective`, so the new directive renders with no body, author or date until reload | **FIXED** — Block 1: the route returns the full directive row with `applies_from` flat beside it | `projects_api.py:1691` vs `GuidancePanel.tsx` |
| F2 | the Attention chip filters `health=attention` by equality, so a `stalled` project — the one that outranks attention — is excluded from the very view meant to surface it | **FIXED** — Block 3: the server expands `health=attention` to `{attention, stalled}` (`_HEALTH_ALIASES`), so the chip's existing query now surfaces both | `projects_api.py` `_list_sync` |
| F3 | `@router.get("/")` / `@router.post("/")` make every list and create pay a 307 (the todos router uses `""`) | **FIXED** — Block 3: both collection routes use `""` (the todos convention); a test pins that they answer without a redirect | `projects_api.py` router |
| F4 | `RunView` never revalidates after a write — no `router.refresh()` on any path | **FIXED** — Block 1: `router.refresh()` on every successful write | `RunView.tsx:62-115` |
| F5 | a `waiting` run is hidden once it falls out of the five-run brief | **FIXED** — Block 3: `_runs_brief` appends the latest waiting run (`projects_db.latest_waiting_run`) when none of the five window rows is waiting — the existing `ProjectDetailView` waiting lookup needs no change | `projects_api.py` `_runs_brief` |
| F6/F7 | upstream error detail/path leakage; 404s rendered as raw load errors | **FIXED** — Block 3: `HermesApiError.message` now carries the upstream `detail` (fixed once at the source in `client.ts`, so every BFF route that renders `err.message` shows the real reason), the projects bridge forwards `err.body.detail` as defense-in-depth, and the three project pages call `notFound()` on a 404 | `client.ts` + `hermes-bridge.ts` + the three `page.tsx` |
| F8 | step 8b (`from_todo`) not on `develop` | **FIXED** — landed in `ffb139319` (#279/#280); see E2 below | `projects_api.py:1400-1519` |

## New findings

Each entry below is written the way the #270 entries are: **where** it is,
**what happens** at runtime, **the fix** against a seam that already exists in
the tree, and **the test** that would have caught it. Line numbers are at
`7c737474f`.

### E1 — "human-only" is enforced in one place, and the agent's own route patches it out (high, contract)

Three acts are human-only by design: accepting an output (§6.1 — the top rung
of the progress ladder), scoring a run (§8.1 — "agent-forbidden" in §16), and
approving a crossing into durable learning (§8.2 — "a human approves every
crossing"). In the shipped code:

- **accept-output has no identity gate at all** — `_require_write(judgement=True)`
  only checks the *role*, and an agent turn acting as a member passes it
  (`projects_api.py:998-1005`).
- **activate-directive has no identity gate either** (`projects_api.py:1717-1728`),
  so the same actor that proposed a directive in its retro can activate it. The
  learning loop closes with no human in it — which is the one thing §8.2 says
  must never happen.
- **score does have the gate** — `_interactive_subject(request)` must return a
  verified session subject (`projects_api.py:2096-2102`). That is the correct
  seam.
- **…and the CLI removes it.** `projects_cli._Api.__init__` monkeypatches
  `projects_api._principal_read`, `_principal_write` **and
  `_interactive_subject`** on the module for the life of the process
  (`projects_cli.py:63-93`), returning the resolved principal's id as the
  "verified subject". Since §14 makes `hermes projects` + `SKILL.md` *the
  agent's* route into Projects, the agent can score its own runs, accept its own
  outputs and activate its own directives. What stops it today is prose in
  `skills/productivity/projects/SKILL.md:107-122` ("Those are human acts by
  design… never your own"), i.e. nothing enforceable.

`--actor` acting as any principal is the inherited `goal_tree_cmd`/`todos_cmd`
convention (`goal_tree_cmd.py:544`) and is *not* a Projects defect — the
machine operator's terminal is trusted. The defect is narrower: the human-only
acts need **one** gate, applied at all three routes, that the agent's own
surface cannot silently satisfy.

**The fix.** Three changes, all local:

1. Extract the gate the score route already has into a helper next to
   `_require_write`, so there is one place to reason about:

   ```python
   async def _require_human(request: Request, act: str) -> str:
       """§8.1/§8.2: a human act needs a verified interactive subject."""
       subject = await _interactive_subject(request)
       if not subject:
           raise HTTPException(
               status_code=403,
               detail=f"{act} is a human act — no verified session, no {act}",
           )
       return subject
   ```

2. Call it in the two routes that lack it — `accept_output_route`
   (`projects_api.py:998-1005`) and `activate_directive_route`
   (`projects_api.py:1717-1728`) — and replace the inline check in
   `score_run_route` (`projects_api.py:2096-2102`) with it. Same for
   `activate_playbook_rev` if the intent is that a lead activates it in person.
   Record the returned subject alongside the existing `by`/`scored_by`
   provenance so the record says *which* human crossed the line.
3. Make the CLI's claim on that gate explicit instead of automatic. Today
   `_Api.__init__` patches `_interactive_subject` unconditionally
   (`projects_cli.py:63-93`). Patch it **only** when the operator passes a new
   `--as-human` flag, and have the three human verbs fail without it:

   ```text
   $ hermes projects score my-proj 7 4
   refused: scoring is a human act (§8.1). Re-run with --as-human if you
   are the operator making this judgement yourself.
   ```

   The principal seams (`_principal_read`/`_principal_write`) keep their
   unconditional patch — that is the machine-operator convention and is fine.

Then delete the prose in `skills/productivity/projects/SKILL.md:107-122` that
describes the rule as if it were enforced, and replace it with what the agent
will actually see: these verbs refuse, and the agent's move is to raise an ask
(§5.3) rather than to score itself.

**The tests.** Through the *router* (no session → 403) for all three acts, and
through the *CLI* — `hermes projects score …` without `--as-human` exits
non-zero and writes nothing. The existing CLI score test passes today only
because the harness patches the gate; that is the test to change.

### E2 — `from_todo.profile` is recorded but never honoured, and a rollback strands the provenance row (medium)

`POST /{slug}/cards` accepts `from_todo: {profile, id}`, and the BFF and the
sheet both forward `profile` (`cards/route.ts:20-36`,
`AddToProjectSheet.tsx:127-145`). The read, however, goes through
`todo_store.default_store()` (`projects_api.py:1436-1446`), which resolves the
**serving process's** configured store (`todo_store.py:353-364`) — the
`profile` value is used only as the label written into
`project_links(kind='todo', profile=…)`. So a promotion naming another profile
either 404s ("to-do not found or not visible") while the UI implies it should
work, or — if an id ever collided — promotes the local to-do while recording
foreign provenance. Either honour the profile in the read (`get_profile_dir` +
a profile-scoped store, under the caller's principal, as
`projects_schedule._cron_in_profile` does) or reject a `profile` that is not
the serving one with a 422 that says so.

**The fix.** The cheap, correct version is to reject what is not supported
rather than to pretend: in `create_card` (`projects_api.py:1436-1446`), if
`from_todo.profile` is present and is not the serving profile, raise

```python
raise HTTPException(
    status_code=422,
    detail="a to-do can only be promoted from the profile serving this "
           "request — open that profile's Projects to promote it there",
)
```

and stop forwarding `profile` from `AddToProjectSheet.tsx:127-145` unless the
sheet actually knows a foreign profile. If cross-profile promotion *is* wanted,
it is a real feature, not a parameter: resolve the profile's store the way
`projects_schedule._cron_in_profile` re-targets `cron.jobs`, read it under the
caller's own principal (never a service principal — that would cross the FG-27
boundary the design forbids), and keep the 404-not-403 behaviour for a to-do
the caller cannot see.

Second half: when the stage move fails, the card is deleted
(`projects_api.py:1505-1516`) but the `project_links` row written inside
`_create_sync` is **not** — the project keeps a `kind='todo'` pointer to a
promotion that did not happen, and because the insert is `INSERT OR IGNORE`,
re-promoting the same to-do later leaves the stale `label`/`added_by` in place.
Roll the link back with the card — `projects_db.remove_project_link` already
exists (`projects_db.py:1845`):

```python
def _rollback_sync() -> None:
    with _board_conn(project) as bconn:
        kanban_db.delete_task(bconn, str(result["task_id"]))
    with projects_db.connect_closing() as pconn:
        projects_db.remove_project_link(
            pconn, project_id=project.id, kind="todo", ref=todo.id
        )
```

**The tests.** A promotion whose `set_stage` raises leaves **no** card *and* no
`kind='todo'` link (the current test only asserts the card is gone); a
promotion naming a foreign profile is refused with 422 and does not touch the
local to-do of the same id.

### E3 — step 11's event tail has no consumer anywhere (medium)

`GET /{slug}/events` exists in Python, in the BFF
(`app/api/projects/[slug]/events/route.ts`), in the client
(`client.ts:1289-1298`) and in the types (`types/index.ts:1580`) — and nothing
calls it. No component polls it, there is no CLI verb, and `SKILL.md` does not
mention it. The detail page still updates only via `router.refresh()` after a
write (`ProjectDetailView.tsx:110,246`), so §12/§13's live board updates are
not realised: a run promoted in the background is invisible until the human
reloads. Under `AGENTS.md` this is the "speculative infrastructure" shape — an
endpoint with no consumer. Either wire the poller (a `since`-cursor hook on the
board panel is a small diff, and `latest_event_id` was built for exactly it) or
drop the endpoint until the consumer lands.

**The fix (wire it).** A `since`-cursor hook, seeded from the first response's
`latest_event_id`, polling while the tab is visible, and calling
`router.refresh()` when the cursor moves — that is the whole feature, because
the server already re-derives progress, health and the rollup on read:

```tsx
// useProjectEvents(slug): poll only while visible; refresh on movement.
const since = useRef(0);
useEffect(() => {
  if (document.visibilityState !== "visible") return;
  const tick = async () => {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(slug)}/events?since=${since.current}`,
    );
    if (!res.ok) return;                       // never surface a poll error
    const data = await res.json();
    if (data.latest_event_id > since.current) {
      since.current = data.latest_event_id;    // head, not the last event
      router.refresh();
    }
  };
  const id = setInterval(tick, 15_000);
  return () => clearInterval(id);
}, [slug, router]);
```

Seed `since.current` from the *first* poll rather than 0, or the first tick
refreshes for history the page already rendered. If the answer is "not in v1",
then delete the route, the BFF route, the client method and the type in the
same PR — the design section can keep the plan.

**The tests.** A component test asserting the hook refreshes when
`latest_event_id` grows and does **not** refresh when it is unchanged; plus a
BFF route test (§Test coverage below) that a poll failure is swallowed rather
than rendered.

The `summarise` half of step 11 is
*not* in this position: the CLI writes it (`projects_cli.py:398-415`), `SKILL.md`
documents it, and the detail header renders it.

### E4 — `events_tail` cannot serve a project with more than ~999 visible cards (low, bounded)

`events_tail` expands every visible task id into the `IN (…)` list of two
queries (`kanban_db.py:3228-3243`). SQLite's default
`SQLITE_MAX_VARIABLE_NUMBER` is 999 on many builds, so a long-running standing
project trips an `OperationalError` and the route 500s — the failure mode
arrives exactly when live updates matter most. It also re-lists every task on
every poll (`projects_api.py:1341-1348`), which is an O(cards) read per client
per interval. A join against `tasks.project_id` with the C2 visibility clause
would replace both the id list and the extra read.

**The fix.** Give `events_tail` a project-scoped overload that never builds an
id list — the visibility clause `list_tasks` already uses is the same one:

```sql
SELECT e.* FROM task_events e JOIN tasks t ON t.id = e.task_id
 WHERE t.project_id = ? AND (<the C2 visibility clause for :principal>)
   AND e.id > ? ORDER BY e.id ASC LIMIT ?
```

with the matching `MAX(e.id)` head query. Keep the current id-list signature
for its existing callers; the route (`projects_api.py:1341-1348`) switches to
the overload and drops its `list_tasks` read entirely.

**The test.** A project with 1,200 visible cards returns a tail rather than
raising `OperationalError` — the invariant is "the tail does not care how many
cards the project has", which is exactly the kind of contract §16 asks for.

### E5 — the derived project score silently disappears behind 25 unscored runs (low)

`_derived_score` reads at most the last 25 runs and *then* takes the first five
scored ones (`projects_api.py:363-373`). A standing project with 25 unscored
recent runs reports no score at all, though §8.1 says the project score is the
mean of the last five *scores*. Either select `WHERE score_user IS NOT NULL
ORDER BY run_no DESC LIMIT 5` in the store, or say in the payload that the
window is the last 25 runs.

**The fix.** Add a store helper beside `list_project_runs`
(`projects_db.py:2302`) and call it from `_derived_score`:

```python
def last_scored_runs(conn, project_id: str, *, limit: int = 5) -> List[dict]:
    """§8.1: the last *scored* runs — the window is scores, not runs."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM project_runs WHERE project_id = ? "
        "AND score_user IS NOT NULL ORDER BY run_no DESC LIMIT ?",
        (project_id, int(limit)),
    ).fetchall()]
```

**The test.** Score runs 1–5, leave runs 6–40 unscored: the project still
reports the mean of runs 1–5. Re-scoring run 1 still moves it (the existing
contract must keep passing).

## Test coverage — what the 185 green tests still do not cover

The suites are behaviour-shaped and genuinely good on the store and the router.
The holes are the same two as before, and both are where every open finding
lives:

1. **The seams are tested through injected fakes only.** `spawn_inline`,
   `cost_reader` and the approval hook all have test doubles, so H1–H4 —
   defects in the *default* implementations of those seams — are invisible to
   the suite by construction. One test per seam that asserts the real default
   resolves a real symbol (`hermes_cli.human_comms.NotificationStore.create`
   exists; `_enabled_toolsets_for_profile("other")` differs from the calling
   process's config; the default spawn passes `profile_home`) would have caught
   all four.
2. **The ~34 BFF routes still have no tests**, and the client tests assert URLs
   rather than response handling — which is why F1a/F1b/F1c survived two review
   passes. One test per write that feeds the *actual* upstream envelope through
   the panel's state update would close the whole class.

Also missing, for the new steps: a test that scoring is refused for a
session-less caller **through the CLI path** (the current CLI test passes
because the CLI patches the gate); a promotion test with a foreign `profile`;
and a `/events` test with more visible cards than the SQLite variable limit.

## Where the feature stands

Steps 1–11 are merged and the design's structure is faithfully built. It is not
deployable as "done" yet, for a short and specific list: the run lifecycle's
three unlanded seams (H1–H4) mean approvals, budgets, capability narrowing and
profile isolation are not actually in force at spawn time; three of the four
write paths on the detail page look like failures to the user (F1a–F1c, F4);
and the human-only acts that make the record trustworthy are enforceable in one
place out of three (E1). None of these is architectural — they are all local
diffs against seams that already exist in the tree.

Recommended order: E1 + F1a/F1b/F1c/F4 (small, and they are what a human sees
and trusts), then H1–H4 (the seams), then M1/M2/F2/F3, then E2–E5.

## Fix checklist — every open item, in the order to do them

21 open items. The recipe for H·/M·/L·/F· lives in
[`2026-08-13-projects-steps-1-8-review.md`](./2026-08-13-projects-steps-1-8-review.md)
(#270) under the same id; the recipe for E· is above. Suggested grouping is one
PR per block — each block is independently shippable and independently testable.

**Block 1 — what a human sees and trusts (do first).**

- [x] E1 · one `_require_human` gate on accept-output, activate-directive and
      score; CLI claims it only under `--as-human`; `SKILL.md` stops asserting
      an unenforced rule. *(landed: the gate covers playbook-rev activation
      too, and the subject rides the `by`/`scored_by` provenance)*
- [x] F1a · `OutputsPanel` handles the ack envelope (or the route returns the
      row); the accepted row must lose its Accept button without a reload, and
      `offers_closure` must surface. *(the route returns the row + the offer;
      the panel merges and surfaces a closure notice)*
- [x] F1b · `RunView` reads `data.run`, and renders `budget_gate` when present —
      today the thing holding the run is invisible.
- [x] F1c · `GuidancePanel` refreshes instead of casting `{id, applies_from}`
      into a `ProjectDirective`. *(the route now returns the full directive
      row with `applies_from` flat beside it)*
- [x] F4 · `router.refresh()` after every `RunView` write.

**Block 2 — the run lifecycle's four seams (the real risk).**

- [x] H1 · approvals through `hermes_cli.human_comms.NotificationStore.create`;
      remove the fail-open `except` — a swallowed approval is worse than a 500.
      *(Block 2: irreversible approvals, owner-targeted, deduped
      `proj:{slug}:run:{n}:{kind}`; store failure logs ERROR and propagates)*
- [x] H2 · bind runs to a real C8 trace (`interactions.create_trace` +
      `bind_trace`), store that `trace_id`, read cost through the shipped
      ledger; only then is `budget_usd_per_run` enforceable.
      *(Block 2: trace minted in `start_run` (mode `prod`, actor = owner),
      bound around the spawn, flushed before the gate; cost = sum of the
      trace's `kind='cost'` events via `InteractionLedger.get_trace` — the
      ledger has no cost column; tracing off → `trace_id` NULL, gate open,
      `cost_recorded` false)*
- [x] H3 · `_enabled_toolsets_for_profile` must read the **host profile's**
      config, not the caller's; narrowing may never grant (invariant 14).
      *(Block 2: resolved inside `profile_runtime_scope`; skills use the same
      scope; unknown profile → empty grant)*
- [x] H4 · pass `profile_home` to `spawn_seeded_session` so a run executes in
      the profile its row records. *(Block 2: host profile's dir +
      `copy_context()` — the trace binding rides the context)*

**Block 3 — health, list and routing.**

- [x] M1 · a repeatable project that has never run is `stalled`.
      *(Block 3: anchor = `last_start` → cron job `created_at` (ISO-parsed via
      `_epoch_from_timestamp`) → `project.created_at`; older than two periods →
      `stalled`)*
- [x] M2 · filter **before** the page slice and take `next_cursor` from the last
      row of the slice, not the last emitted row (today paging loses rows).
      *(Block 3: single newest-first pass; filters apply per row before it is
      appended; the cursor is the last **examined** row, so an all-filtered
      page still returns a cursor and pagination neither loses nor repeats
      rows)*
- [x] F2 · the Attention chip must include `stalled`, which outranks attention.
      *(Block 3: server-side `_HEALTH_ALIASES` expands `health=attention` to
      `{attention, stalled}`; the chip's query is unchanged)*
- [x] F3 · `@router.get("")` / `@router.post("")` — drop the 307 on every list
      and create. *(Block 3: both collection routes use `""`; a test asserts
      they answer without a redirect)*
- [x] M3 · an instance owner/admin who is not a member still sees contact
      addresses. *(Block 3: `include_address` follows write authority —
      `_can_write` or role lead/editor)*
- [x] F5 · a `waiting` run is always in the brief, however old.
      *(Block 3: `_runs_brief` appends `projects_db.latest_waiting_run` when
      the five-run window holds no waiting row; the detail view's existing
      waiting lookup is unchanged)*
- [x] F6/F7 · stop leaking upstream detail through the BFF; render a 404 as
      "no such project", not as a load error.
      *(Block 3: `HermesApiError.message` carries the upstream `detail`
      (fixed at the source in `client.ts` so every route rendering
      `err.message` shows the real reason); the projects bridge forwards
      `err.body.detail`; the three project pages call `notFound()` on a 404
      and render generic copy)*

**Block 4 — the later steps.**

- [x] E2 · reject (or honour) a foreign `from_todo.profile`; roll the
      `project_links` row back with the card. *(Block 4: the seam honours
      only the profile serving the request — a foreign `profile` is a 422
      before the to-do store is touched; a `set_stage` failure now rolls
      the `project_links(kind='todo')` row back with the card; the sheet
      names no profile of its own)*
- [x] E3 · wire the event tail to the detail page, or delete the endpoint.
      *(Block 4: wired — `useProjectEvents` polls `/events` every 15s,
      seeding its cursor from the first response's `latest_event_id` and
      `router.refresh()`-ing when the head moves; mounted in
      `ProjectDetailView`)*
- [x] E4 · project-scoped `events_tail` join, so >999 cards does not 500.
      *(Block 4: `kanban_db.project_events_tail` — one join against
      `tasks.project_id` with the C2 visibility clause; no id list, no
      card re-list)*
- [x] E5 · derive the project score from the last five *scores*, not from
      scores within the last 25 runs. *(Block 4:
      `projects_db.last_scored_runs` — the window is the last five
      `score_user` rows; an unscored streak can no longer hide the score,
      and a re-score still moves it)*
- [x] L1 · `toolsets`/`skills` as rows rather than CSV. *(Block 4: the
      recipe's cheapest-correct option — CSV kept, names validated against
      `^[A-Za-z0-9_.:-]+$` at write time, before the unknown-name check)*
- [x] L2 · profile-imported legacy projects violate the mandatory-field
      invariant (NULL `goal`, no outputs, no host profile) — decide whether they
      are quarantined, completed on first open, or migrated. *(Block 4:
      quarantined — `needs_completion` status; activation and scheduling
      refuse with the missing fields named, doctor flags it, the list row
      shows "needs completion" instead of an empty goal)*

**Block 4b — U1: give the Projects page its create and remove doors**
(owner report, 2026-08-18; FG-32 §12, §13, §20.2 item 6 and decision 17 now
specify this — read them before starting, the delete rules are deliberate).

- [x] `POST /{slug}/archive` — `archived=1` **and** `status='archived'` in one
      transaction, plus `detach_project_schedule()`; lead/admin; returns the
      updated project row, not an ack (Block 1's lesson). *(Block 4b:
      `projects_db.archive_project` sets both flags in one `write_txn` and
      reuses the `_needs_completion_missing` gate — a `needs_completion`
      project is refused with 409; the route detaches the schedule and
      records an optional `reason` as a directive, ValueError → 409 like the
      members/outputs siblings; `restore_project` sets `paused` and never
      resurrects the cron job. §16 contracts in
      `tests/hermes_cli/test_projects_api_lifecycle.py`)*
- [x] `POST /{slug}/restore` — `archived=0`, `status='paused'`; does not
      re-create the cron job. *(Block 4b: refuses a not-archived project with
      409; the restored row comes back `paused` — a lead decides when it runs
      again)*
- [x] `DELETE /{slug}?confirm=<slug>` — `_require_human`, owner/lead, and `409`
      unless the project is already archived and has no run, no
      delivered/accepted output and no card. **Do not cascade past the FK:**
      `tasks.project_id` lives in the per-profile kanban store with no foreign
      key back to `projects`, so a permissive delete orphans board rows. Clear
      the `project_meta` active pointer and detach the schedule first.
      *(Block 4b: `confirm` must equal the slug (422 otherwise); cards are
      counted through `kanban_db.list_tasks(..., include_archived=True)` so
      archived cards still block; the 409 names every blocker — "Archive
      keeps it; hard delete is only for the genuinely empty mistake")*
- [x] BFF: `POST /api/projects/[slug]/archive`, `.../restore`, `DELETE
      /api/projects/[slug]`; `archiveProject()`, `restoreProject()`,
      `deleteProject()` on the client; preserve upstream status codes so the
      dialog can say *why* a delete was refused. *(Block 4b: the three routes
      go through `withPrincipal`, which forwards the upstream status and the
      string `detail` verbatim; DELETE reads `confirm` from the query string)*
- [x] agent-home: `/projects/new` + `NewProjectForm` (the §13 two-step form,
      422 → the field that is blank, typed input preserved on refusal), a
      primary **New project** action in the list header *and* as the empty
      state's CTA, `ProjectLifecycleMenu` in the detail header's `[⋯]`
      (Archive… / Restore / Delete permanently…, typed-slug confirm), and an
      Archived chip beside All. *(Block 4b: the form pins the host profile to
      the serving profile; step 1 is the mandatory what-fields, step 2 the
      how-it-runs defaults; a viewer sees no `[⋯]` at all and a non-lead
      member sees Archive disabled with the reason; Delete only appears when
      the rendered record is archived and genuinely empty — the server
      re-checks everything. Archived chip decodes
      `archived=true&status=archived`)*
- [x] CLI: `hermes projects archive|restore|delete <slug>`, delete behind
      `--as-human --confirm <slug>`. *(Block 4b: archive/restore are role-gated
      only; delete additionally passes `--as-human` through the
      `_interactive_subject` seam and `--confirm` as the query param; a 403
      "human act" prints the `--as-human` hint)*
- [x] Tests: the FG-32 §16 "Lifecycle" contracts, plus one asserting a rendered
      surface routes to `/projects/new` — a client method with no caller is the
      exact shape of this defect. *(Block 4b: 15 lifecycle contracts in
      `test_projects_api_lifecycle.py` + 2 CLI verb tests in
      `test_hermes_projects.py`; on the UI side `ProjectsList.test.tsx`
      asserts the create door in the header and the empty state, and
      `ProjectLifecycleMenu.test.tsx` + `NewProjectForm.test.tsx` cover the
      role matrix and the step-1 surface)*

**Block 5 — close the two holes that hid all of the above.**

- [x] One contract per run seam asserting the *default* implementation resolves
      a real symbol (this is what makes H1–H4 impossible to reintroduce).
      *(Block 5: `tests/hermes_cli/test_projects_run_seams_defaults.py` —
      the REAL seam functions, no seam monkeypatching: H1 resolves the
      shipped `NotificationStore` and refuses a non-Supabase store; H2 walks
      `InteractionLedger.get_trace` and sums `kind='cost'` summaries, reading
      "not recorded" without a DSN; H3 reads the profile home's config inside
      `profile_runtime_scope`, never the caller's, and an unknown profile
      grants nothing; H4 passes the host profile's home, origin and a copied
      context to `spawn_seeded_session`)*
- [x] One test per BFF write feeding the actual upstream envelope through the
      panel's state update (this is the whole F1 class). *(Block 5:
      `envelopes.ts` collects the three write envelopes into pure
      state-update helpers (accept merge + closure offer, continue/cancel run
      unwrap + budget gate, directive prepend); `envelopes.test.ts` feeds the
      exact upstream answers through them and
      `api/projects/write-envelopes.test.ts` proves the mirror routes pass
      those envelopes through untouched)*
- [x] The three §16 contracts with no test today: CLI scoring refused without a
      human, foreign-profile promotion refused, event tail above the SQLite
      variable limit. *(All three landed before Block 5 reached them: CLI
      scoring by Block 1's `test_score_verb_refuses_without_as_human` /
      `test_score_verb_with_as_human_is_the_operator` in
      `test_hermes_projects.py` (the fixture patches only the principal
      lookup, so the router's real session gate runs); foreign-profile
      promotion by Block 4's
      `test_promote_foreign_profile_is_refused_before_touching_the_todo`; the
      >999-card event tail by Block 4's
      `test_events_tail_survives_more_than_999_cards`)*
