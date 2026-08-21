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

## Block 4b follow-up review (2026-08-20) — findings U2–U6

Reviewed at `570be680f` (`2d187f122`, "Block 4b — create/remove doors"), against
FG-32 §12, §13, §16 "Frontend"/"Lifecycle" and decision 17. The lifecycle
backend matches the design: archive writes both halves in one `write_txn` and
detaches the schedule in the same call, restore lands `paused` and never
resurrects cron, hard delete is human-gated with a typed slug and names every
blocker, archived cards block through
`kanban_db.list_tasks(..., include_archived=True)`, the active pointer is
cleared and the cascade stops at the projects DB. Verified green locally:
31 tests in `test_projects_api_lifecycle.py` + `test_hermes_projects.py`,
154 agent-home tests, `tsc --noEmit` clean.

Five things the block did not land. **All five are fixed in Block 4c
(#307, `9fcdf03c4`)** — the archived-inert gate, the structured `missing`
forwarding, the interaction + route tests, the archived-inclusive card count
and the flag-aware Archived chip; each item below carries the annotation of
what pinned it, and the Block 4c checklist at the end of this document is
fully ticked.

### U2 — archive stops the cron job, not the project (medium, contract)

§13 says archive "drops the project out of the list, **and stops it running**",
and §16 "Lifecycle" says the archived detail page renders the record with
**restore as the only write offered**. Neither is enforced:

- No route in `hermes_cli/projects_api.py` reads `archived` except the three
  lifecycle routes and the list filter (`grep -n archived` → lines 503, 510,
  591, 729, 923-1055 only). So `POST /{slug}/runs`, `POST /{slug}/outputs/{id}/accept`,
  `POST /{slug}/directives`, `PUT /{slug}/schedule` and `PATCH /{slug}/tools`
  all still succeed on an archived project. A shelved project can be run by
  hand from the CLI or the API — only the *timer* was removed.
- On the page, `ProjectDetailView.tsx:181-217` hides the three header buttons
  (Run now / Continue / Add) when `project.archived`, but the panels below it
  never look at the flag: `panels/OutputsPanel.tsx:45` and
  `panels/GuidancePanel.tsx:42,69,87,107` still render and fire their writes.

**The fix.** Refuse the mutating routes on an archived record at the router —
a small helper beside `_require_write`:

```python
def _refuse_if_archived(project, act: str) -> None:
    """§13: a shelved project does not run and does not learn. Restore first."""
    if project.archived:
        raise HTTPException(
            status_code=409,
            detail=f"project {project.slug} is archived — restore it before {act}",
        )
```

Call it from the run, accept, directive, schedule and tools routes (not from
`PATCH /{slug}`, so a typo in an archived project's goal can still be fixed,
and not from restore). Then pass `archived` down from `ProjectDetailView` and
have `OutputsPanel`/`GuidancePanel` render their write affordances as disabled
with "Restore this project to …" rather than firing a call the server now
refuses.

**The test.** Archive a project, then assert each mutating route answers 409
and that the record is unchanged; and one UI contract that the archived detail
markup contains no accept/add-instruction control.

### U3 — the 422 → blank-field contract is dead code (medium)

§13: "the BFF's 422 `missing` list maps onto the field that is blank", and §16
Frontend: "a 422 from create names the blank field in the form rather than a
toast". `NewProjectForm.tsx:82-110` implements exactly that — and it can never
run:

- `withPrincipal` (`agent-home/src/app/api/projects/hermes-bridge.ts:31-44`)
  forwards `err.body.detail` **only when it is a string**, otherwise it
  substitutes `"That didn't go through."`. The Python create route raises
  `detail={"missing": [...], "message": ...}`
  (`projects_api.py`, `create_project_route`) — a dict — so the `missing` list
  is dropped at the bridge.
- The BFF's own pre-check answers `{error: "invalid_request", detail: "<string>"}`
  (`app/api/projects/route.ts:69-79`), also without a `missing` list.

Net effect: every server refusal shows generic copy with no field highlighted;
the field-mapping branch is unreachable. Typed input *is* preserved (client
state), so only the targeting is lost.

**The fix.** Let the bridge forward a structured detail rather than flattening
it — `detail` stays a string for the existing callers, and the object is
carried alongside:

```ts
const raw = (err.body as { detail?: unknown } | undefined)?.detail;
const detail = typeof raw === "string" && raw ? raw : "That didn't go through.";
return NextResponse.json(
  raw && typeof raw === "object" ? { error: "api_error", detail, ...raw } : { error: "api_error", detail },
  { status: err.status },
);
```

…or, simpler and local: have `app/api/projects/route.ts`'s pre-check answer
`{error: "invalid_request", detail: {missing: [...], message: "…"}}` and read
that shape in the form. Either way one of the two ends has to speak `missing`.

**The test.** A route test where upstream raises a dict-detail 422 and the BFF
answer still carries `missing: ["outputs"]`, plus a form test asserting the
output field is marked invalid and the typed goal survives.

### U4 — the new create/remove surface has no interaction tests (medium)

This is the same hole U1 came out of. `archiveProject`/`restoreProject`/
`deleteProject` have no test at all (`grep -rn archiveProject src --include=*.test.*`
→ nothing), the three new BFF routes have none (route tests are an established
pattern in this tree — `app/api/chat/send/route.test.ts`,
`app/api/profiles/suggestions/route.test.ts`, …), and the three new component
tests are `renderToStaticMarkup` string assertions only. Nothing exercises:
submit → `/projects/<slug>` redirect; a 422 mapping onto a field (U3 above,
which a single test would have caught); typed input surviving a refusal;
archive/restore/delete actually calling the route and refreshing; the delete
button staying disabled until the slug matches.

**The fix.** Three route tests (archive/restore/delete: principal missing →
401, upstream 409 → 409 with the refusal text, DELETE forwards `confirm`) and
form/menu tests that drive the handlers with a stubbed `fetch`.

### U5 — `deleteEligible` and the server disagree about archived cards (low)

`ProjectLifecycleMenu.tsx:53-57` gates Delete on
`project.card_rollup.total === 0`, but the rollup is built from
`kanban_db.list_tasks(bconn, project_id=…, principal=…)`
(`projects_api.py:253-254`) — `include_archived` defaults to `False`, while the
delete route counts archived cards too. A project whose only cards are archived
therefore *offers* Delete and then takes a 409. The refusal text is shown, so
this is cosmetic; fix it by adding an archived-inclusive count to the detail
payload (or by having the menu treat "no cards" as unknown and let the server
answer).

### U6 — the Archived chip cannot find a legacy archived row (low)

The chip encodes `archived=true&status=archived`
(`components/projects/filters.ts:95-98`) and the list filters `p.status != status`
(`projects_api.py:535`). Any row with `archived = 1` and a status other than
`'archived'` — anything shelved before Block 4b, or by a direct
`PATCH`/store call — appears under **All** but never under **Archived**, which
is the one place §13 says a shelved project must be findable. Either treat
`status=archived` as "archived flag or archived status" in `_list_sync`, or
backfill `status='archived' WHERE archived = 1` in the migration that follows.

**Not a defect:** `/projects/new` passes `servingProfile="default"`. Throughout
agent-home `"default"` *means* the box's own home (`lib/api/client.ts:139,158`;
`profiles.get_profile_dir` resolves it to `_get_default_hermes_home()`), and the
projects BFF talks to that home, so the pinned value is the serving profile. The
consequence worth writing into §13 is that the UI cannot create a project hosted
on a *named* profile — that stays a CLI act.

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

**Block 4c — finish the lifecycle Block 4b started** (findings U2–U6 above,
in this order).

- [x] **U2** Refuse the mutating routes on an archived project (`_refuse_if_archived`
      in `projects_api.py`, called from run / accept / directives / schedule /
      tools — not from `PATCH /{slug}` or restore), and pass `archived` into
      `OutputsPanel` / `GuidancePanel` so the archived detail page offers
      restore and nothing else (§13, §16 "Lifecycle"). *(Block 4c: the helper
      guards all five routes with a 409 naming the archive and pointing at
      restore; both panels hide their writes behind the same hint;
      `test_archived_project_refuses_every_mutating_route` and
      `test_archived_project_still_accepts_a_patch_and_restore_unblocks` pin
      the contract)*
- [x] **U3** Make one end speak `missing`: either stop `withPrincipal` flattening
      a dict `detail` (`app/api/projects/hermes-bridge.ts:31-44`) or have the
      create route's pre-check answer a structured detail — so
      `NewProjectForm`'s field mapping can actually fire (§13, §16 Frontend).
      *(Block 4c, both ends: `withPrincipal` now forwards a dict detail's
      `message` as the string `detail` with the remaining keys — `missing` —
      riding top-level, and the create route's pre-check answers its own
      `missing` list; `NewProjectForm.interaction.test.tsx` drives a 422 onto
      the blank field and keeps what was typed)*
- [x] **U4** Tests for the surface Block 4b added: route tests for
      archive/restore/delete (401 without a principal, upstream 409 forwarded
      with its refusal text, `confirm` forwarded on DELETE) and handler-level
      form/menu tests (submit → redirect, 422 → field marked, typed input
      preserved, Delete disabled until the slug matches). *(Block 4c: three
      `route.test.ts` files under `api/projects/[slug]/` — 401, forwarded 409
      wording, `reason`/`confirm` forwarding — plus
      `NewProjectForm.interaction.test.tsx` and
      `ProjectLifecycleMenu.interaction.test.tsx`, jsdom handler tests in the
      `apps/desktop` per-file-environment pattern; the matching devDeps ride
      with the tests)*
- [x] **U5** Give the detail payload an archived-inclusive card count so
      `ProjectLifecycleMenu`'s `deleteEligible` agrees with the delete route.
      *(Block 4c: `card_rollup.total_with_archived` from one
      `include_archived=True` query; the menu's gate reads it;
      `test_detail_rollup_counts_archived_cards_for_the_delete_gate` pins it)*
- [x] **U6** Make Archived find every shelved row — `status=archived` should
      match the archived *flag*, or backfill `status` for rows archived before
      Block 4b. *(Block 4c, filter-on-the-flag: a `status=archived` list also
      matches `archived=1` rows; `test_archived_chip_finds_legacy_rows_shelved_by_flag_alone`
      pins it against a flag-only legacy row)*
- [x] Note in FG-32 §13 that the create form pins the host profile to the
      serving home, so hosting a project on a *named* profile stays a CLI act
      (the design's intent, not a gap — recorded so it is not re-reported).
      *(Block 4c: recorded in FG-32 §13's create-form paragraph; the same
      edit records the archived-inert rule in §13 and §16)*

## Block 4c follow-up review (2026-08-21) — findings U7–U8

Reviewed at `b743715d1` (`9fcdf03c4`, "Block 4c — archived projects stay inert",
PR #307, open against `develop` at the time of review) against FG-32 §12, §13,
§16 "Lifecycle" and decision 17. Every line number below is on that branch, not
on `develop`.

**U3, U4, U5 and U6 are genuinely closed.** The bridge forwards a structured
`detail` (`message` becomes the string, the remaining keys ride top-level) and
the create route's own pre-check answers the same `missing` shape, so
`NewProjectForm`'s field mapping fires from either end; the three lifecycle BFF
routes and both new components have handler-level tests; `card_rollup` carries
`total_with_archived` from one `include_archived=True` query and the menu's
`deleteEligible` reads it; and a `status=archived` list matches the archived
*flag* as well as the status, so a flag-only legacy row is reachable from the
chip. `total` is unchanged by U5's rewrite because `archived` is a kanban
*status*, so the new loop's `continue` drops exactly the rows the old
`include_archived=False` query never returned. Verified locally at that sha:
310 Projects Python tests + 1 skipped, 517 agent-home tests across 80 files,
`tsc --noEmit` clean, `ruff check` clean on the touched files (the 8 eslint
errors in the tree are pre-existing, in `chat/` and `settings/` files Block 4c
does not touch). The de-flake of
`test_list_pagination_loses_no_rows_under_a_filter` (staggering `created_at` so
"newest first" is not a coin flip on random ids) is a real determinism fix with
no product change.

Two things remain.

### U7 — "a shelved project does not run and does not learn" is asserted in the design but enforced on five routes only (medium, contract)

`_refuse_if_archived` (`hermes_cli/projects_api.py:220`) is the right helper in
the right place, and it is called from exactly the five routes Block 4c's
worklist named: `POST /{slug}/runs` (2154), `POST
/{slug}/outputs/{id}/accept` (1266), `POST /{slug}/directives` (1961), `PATCH
/{slug}/tools` (2481), `PUT /{slug}/schedule` (2587). That list came from the
U2 write-up, which named the routes it had found — it was never the complete
set of acts that grow the record.

The same block then widened the *contract* to the general rule. FG-32 §13 now
says "every mutating act that would grow the record … is refused `409`", and
§16 "Lifecycle" repeats it. Against that wording these routes are holes — all
of them still answer 200 on an archived project:

| Route | Line | What it does on a shelved project |
| --- | --- | --- |
| `POST /{slug}/runs/{run_no}/continue` | 2183 | Resumes a `waiting` run: promotes the held successors to `todo`, so workers pick them up. The project runs again with no restore. |
| `POST /{slug}/runs/{run_no}/retro` | 2282 | Writes the retro — and the retro is the entry point of the learning loop (§10). |
| `POST /{slug}/runs/{run_no}/score` | 2390 | Records the human 1–5, which moves the project's score rollup. |
| `POST /{slug}/cards` | 1663 | Adds a board card to an archived project — re-creating the very orphan class hard delete's card blocker exists to prevent (§12). |
| `POST /{slug}/playbook`, `POST /{slug}/playbook/{rev}/activate` | 1860, 1907 | Saves and activates a plan revision, the second of the three learning destinations. |
| `POST /{slug}/outputs`, `POST /{slug}/outputs/{id}/deliver` | 1130, 1219 | Declares a new commitment / marks one delivered, so the project can acquire delivered work while shelved. |
| `PATCH /{slug}/autonomy` | 2548 | Raises how much the project may do unattended. |
| `POST /{slug}/summarise` | 1629 | Rewrites "where this stands" on a record that is meant to be frozen. |

`continue` is the one that breaks the invariant outright rather than
cosmetically, and it has a live path: **archive has no precondition on an
in-flight run.** `archive_project_route` (957) refuses only an
already-archived record, so a project with a `waiting` run can be shelved while
that run is held at a checkpoint or a budget stop; the run row survives
untouched, and the run page still offers Continue. Detaching the cron job (the
one thing archive does do) removes the *timer*, not the resumable run.

The UI mirrors the same partial fix. `ProjectDetailView.tsx:181` hides the
header's three actions and passes `archived` into `OutputsPanel` and
`GuidancePanel`, but the run page does not:
`app/projects/[slug]/runs/[runNo]/page.tsx:64` renders
`<RunView slug={slug} run={run} />` — it fetches the run and never the project,
so `RunView` has no way to know the project is archived and keeps rendering
Continue, Cancel, Save retro and the score control. The CLI needs no separate
fix: every verb goes through this router (`_cmd_run` → `POST /{slug}/runs`), so
it inherits whatever the router enforces — which is exactly why the router is
the right place to finish the job.

#### U7 illustrated

**(1) Where the gate sits today.** One helper, thirteen doors, five of them
wired to it:

```
                        POST /{slug}/runs ─────────────┐
                        POST /outputs/{id}/accept ─────┤
                        POST /directives ──────────────┼──▶ _refuse_if_archived
                        PATCH /tools ──────────────────┤        (409 if archived)
                        PUT  /schedule ────────────────┘

    POST /runs/{n}/continue ──┐
    POST /runs/{n}/retro ─────┤
    POST /runs/{n}/score ─────┤
    POST /cards ──────────────┤
    POST /playbook (+activate)┼──▶ (no check) ──▶ 200, record grows
    POST /outputs (+deliver) ─┤
    PATCH /autonomy ──────────┤
    POST /summarise ──────────┘
```

The five above the line are the ones the Block 4c worklist happened to name
(they were the ones the U2 write-up had found). The eight below are the same
kind of act and were never gated — while FG-32 §13/§16 now promise the general
rule ("every mutating act that would grow the record is refused 409").

**(2) The live path — archiving does not stop a run that is already in flight.**
Time runs downward; the left column is what a human does, the right is what the
system holds:

```
   human / agent                       project state
   ─────────────────────────────────────────────────────────────────────
   POST /{slug}/runs            ──▶    run 7: running
                                       cards: [a done][b done][c todo]
   run hits a checkpoint        ──▶    run 7: waiting          ← held
                                       card c: blocked (awaiting approval)
                                       cron job: attached

   POST /{slug}/archive         ──▶    archived = 1
     (no precondition on an            status   = archived
      in-flight run)                   cron job: DETACHED  ← the only stop
                                       run 7:   waiting     ← untouched
                                       card c:  blocked     ← untouched

   ── the project is now "shelved": UI hides Run / Add directive / Accept ──
   ── but the run page still renders Continue, and the route still answers ──

   POST /runs/7/continue        ──▶    run 7: running        ← RUNNING AGAIN
     (ungated)                         card c: todo          ← promoted
                                       workers pick c up, the agent works,
                                       outputs get produced, cost is spent
```

So archive removed the **timer** (the cron job), not the **resumable run**. A
shelved project can still be doing work minutes later, with no restore, and the
record it was supposed to freeze keeps growing. `POST /cards` is the second
substantive one: it lets a shelved project acquire new board rows — the exact
orphan class hard delete's card blocker exists to prevent.

**(3) The fix, as a picture.** Two edges to add, and one deliberate
non-edge (`cancel` stays open — it *reduces* the record):

```
   POST /{slug}/archive
        │
        ├─ run in running/waiting? ──▶ 409 "run 7 is still open —
        │                               cancel or continue it first"   ← NEW
        └─ else ──▶ archived = 1, schedule detached

   any growing route on an archived project
        │
        └─▶ _refuse_if_archived ──▶ 409                                ← NEW
                                    (continue, retro, score, cards,
                                     playbook/activate, outputs/deliver,
                                     autonomy, summarise)

   POST /runs/{n}/cancel ──▶ still allowed                        ← UNCHANGED

   runs/[runNo]/page.tsx
        │  fetches: run                     fetches: run + project   ← NEW
        └─▶ <RunView run />        ──▶      <RunView run archived /> ← NEW
                                            hides Continue / retro / score
                                            behind "restore it (⋯) to …"
```

With the archive-time precondition in place, "can I score a run on a shelved
project?" cannot arise for a run that was in flight — the only runs on an
archived project are finished ones, so scoring them afterwards can stay allowed
as a deliberate exception (a human closing the book on work already done) if
you prefer that to a blanket refusal. Write down whichever you pick.

**The fix — pick one of the two, do not leave them disagreeing.**

*(a) Enforce the general rule (preferred; it is what §13 now promises).* Call
`_refuse_if_archived` from the eight routes above, with the act named as the
existing calls do. Two of them want a thought, not a reflex:

- `cancel` must stay allowed — it *reduces* the record, and cancelling a run
  left waiting on a project someone shelved is the natural cleanup. So gate
  `continue`, not `cancel`.
- `retro`/`score` are the reason to prefer the stronger variant: refuse
  archiving while a run is not terminal. Add to `_archive_sync`, beside the
  already-archived check, a blocker for any run in `running`/`waiting` —
  "refused: run N is still open — cancel or continue it first" — so the
  question "can I score a run on a shelved project?" cannot arise for a run
  that was in flight, and scoring a *finished* run afterwards can then stay
  allowed as a deliberate exception (a human closing the book on work that is
  already done). Record whichever way you choose in §13; the current wording
  forbids it.

Then pass `archived` into the run surface: have
`app/projects/[slug]/runs/[runNo]/page.tsx` also fetch the project (it already
holds an `apiClientForRequest()`), pass `archived` to `RunView`, and have
`RunView` render the write affordances behind the same "restore it (⋯) to …"
hint the panels use.

*(b) Or narrow the contract.* If the intent really is only the five, change
FG-32 §13 and §16 to enumerate them and say plainly which acts stay open on a
shelved project and why. Then U7 is a documentation fix — but `continue`
resuming a shelved project still needs to be either gated or written down as
intended, because "archive … stops it running" (§13, unchanged since #304)
cannot coexist with it.

**The test.** Extend
`test_archived_project_refuses_every_mutating_route` to the full set rather
than adding a second test — it is already a table, and the table *being* the
enumeration is what stops the next route from arriving ungated. Plus: archive a
project holding a `waiting` run and assert whichever rule you chose (refused at
archive, or 409 at `continue`), and one UI contract that the archived project's
run page markup carries no Continue/Save-retro control.

### U8 — the delete gate and the delete route still count cards through different eyes (low)

U5 closed the archived half of this. The other half is the principal.
`_card_rollup_sync` (`projects_api.py:268`) counts through
`kanban_db.list_tasks(..., principal=principal)`, which for a non-owner adds
`AND (visibility = 'shared' OR visibility = ?)`; the delete route's own count
(1067) passes **no** principal, deliberately — "an orphan is an orphan whatever
its column", and the same is true whoever created it. So a lead whose colleague
holds a private card on the project sees `total_with_archived == 0`, is offered
Delete, and takes the 409 the count was supposed to pre-empt.

Same cosmetic class as U5 (the refusal text is shown, nothing is destroyed),
and the fix should not leak the card: add an unfiltered count to the payload —
`card_rollup.total_all_principals`, or simply have `_card_rollup_sync` compute
the archived-and-visibility-inclusive number with
`SELECT COUNT(*) FROM tasks WHERE project_id = ?` — and have `deleteEligible`
read that. It is a count, not a card: no title, no assignee, no visibility
leaks with it.

**The test.** Two cards on a project, one private to another user; assert the
detail payload's delete-gate count is 2 for a lead who can see only one of
them, and that `deleteEligible` is therefore false.

**Block 4d — finish U7, and U8 with it.**

**All three landed in Block 4d (#310, `59d39ff50`)** — the gate now covers every
growing route with the deliberately-open list written into the helper,
archive refuses an open run, the run page knows the project is archived,
and the delete gate counts principal-blind; each item below carries the
annotation of what pinned it.

- [x] **U7** Make the code and FG-32 §13/§16 agree: either call
      `_refuse_if_archived` from `continue`, `retro`, `score`, `cards`,
      `playbook` (+ activate), `outputs` (+ deliver), `autonomy` and
      `summarise` — leaving `cancel` and `PATCH /{slug}` open — or narrow the
      design's wording to the five routes Block 4c gated. Either way,
      `continue` must not resume a shelved project silently, and archive
      should refuse (or the design should permit) shelving a project whose run
      is still `running`/`waiting`.
      *(Block 4d: the first branch — `_refuse_if_archived` now runs from all
      twelve growing routes, the deliberately-open list (PATCH, restore,
      cancel, the DELETE verbs, directive retirement, bookkeeping POSTs) is
      written into the helper's docstring, and `_archive_sync` refuses a
      `running`/`waiting` run naming it, with cancel as the sanctioned way
      out. The refusal table in `test_projects_api_lifecycle.py` enumerates
      all seventeen mutating attempts; `test_archive_refuses_while_a_run_is_open`
      and `test_cancel_still_works_on_an_archived_project` pin both halves.)*
- [x] **U7 (UI)** `app/projects/[slug]/runs/[runNo]/page.tsx` fetches the run
      only, so `RunView` cannot know the project is archived and still offers
      Continue / Cancel / Save retro / the score control. Pass `archived`
      down and hide the writes behind the panels' hint.
      *(Block 4d: the run page fetches the project alongside the run —
      a failed project fetch renders unflagged rather than erroring — and
      `RunView` hides Continue / Repeat this run / Save retro / the score
      control behind the panels' hint while Cancel stays; `RunView.test.tsx`
      pins the archived and the live renderings.)*
- [x] **U8** Give the delete gate a principal-blind card count
      (`total_all_principals`, or a plain `COUNT(*)` in `_card_rollup_sync`)
      so a lead who cannot see a colleague's private card is not offered a
      Delete the route will refuse.
      *(Block 4d: `_card_rollup_sync` adds `total_all_principals`, a
      principal-blind `COUNT(*)` over `tasks.project_id` (a count leaks no
      card); `ProjectLifecycleMenu.deleteEligible` reads it, and
      `test_delete_gate_counts_cards_it_cannot_see` pins the
      sees-one / counts-two split with the consistent 409.)*

### Block 4d — the implementation plan

Written against `develop` at `6620a22e6`; every line number below is that sha.
Five steps, in order — steps 1–2 are one PR (they are one decision), step 3 is
the surface that step 2 makes truthful, step 4 is independent and can go first
if you want a warm-up. Do not start step 3 before step 2: the UI must hide only
what the router already refuses, or the next reviewer finds the same split
again.

#### Step 1 — archive refuses a project whose run is still open

**Why first.** It removes the hard case from step 2. If no archived project can
own a `running`/`waiting` run, then "may I continue / retro / score a run on a
shelved project?" only ever concerns *finished* runs, and the answer is a
policy choice rather than a correctness hole.

`_archive_sync` (`hermes_cli/projects_api.py:974`) already refuses one thing —
an already-archived record — and raises `ValueError`, which the wrapper at 1010
turns into a `409`. Add a second blocker in the same style, right after the
`fresh.archived` check and *before* `projects_db.archive_project`:

```python
open_runs = [
    r for r in projects_db.list_project_runs(conn, fresh.id, limit=50)
    if r["status"] in ("running", "waiting")
]
if open_runs:
    held = ", ".join(f"run {r['run_no']} ({r['status']})" for r in open_runs)
    raise ValueError(
        f"project {fresh.slug} still has an open run — {held}. "
        "Cancel it (or let it finish) before archiving."
    )
```

`VALID_RUN_STATUSES` is `running, waiting, blocked, done, failed, cancelled`
(`projects_db.py:2315`). Block only `running` and `waiting`: `blocked` is a
card-level wait that carries no resume affordance, and `done`/`failed`/
`cancelled` are terminal. Cancel is the escape hatch, and it already works on
an archived project — which is the second reason step 2 must leave `cancel`
open.

#### Step 2 — call `_refuse_if_archived` from every act that grows the record

The helper (`projects_api.py:220`) needs no change; it takes the act name and
raises the `409` whose text names restore. Add these calls, each immediately
after the route's `_require_write(...)`/`_require_human(...)` line so the gate
runs before any body parsing or DB work:

| Route | Line | `act` string |
| --- | --- | --- |
| `POST /{slug}/runs/{run_no}/continue` | 2183 | `"continuing a run"` |
| `POST /{slug}/runs/{run_no}/retro` | 2282 | `"writing a retro"` |
| `POST /{slug}/runs/{run_no}/score` | 2390 | `"scoring a run"` |
| `POST /{slug}/cards` | 1663 | `"adding a card"` |
| `POST /{slug}/playbook` | 1860 | `"revising its plan"` |
| `POST /{slug}/playbook/{rev}/activate` | 1907 | `"activating a plan"` |
| `POST /{slug}/outputs` | 1130 | `"declaring an output"` |
| `POST /{slug}/outputs/{output_id}/deliver` | 1219 | `"delivering an output"` |
| `PATCH /{slug}/outputs/{output_id}` | 1158 | `"changing an output"` |
| `PATCH /{slug}/autonomy` | 2548 | `"changing its autonomy"` |
| `POST /{slug}/summarise` | 1629 | `"re-summarising it"` |
| `POST /{slug}/directives/{id}/activate` | 2014 | `"activating guidance"` |

**Deliberately left open — write this list into the helper's docstring so the
next reader does not "finish the job" again:**

- `PATCH /{slug}` — already documented: a typo in an archived goal is still
  fixable, and `test_archived_project_still_accepts_a_patch_and_restore_unblocks`
  pins it.
- `POST /{slug}/restore` — the one write the detail page offers.
- `POST /{slug}/runs/{run_no}/cancel` — cancelling *reduces* the record, and
  step 1 makes it the sanctioned way out of an open run.
- `POST /{slug}/directives/{id}/retire`, `DELETE /{slug}/outputs/{id}`,
  `DELETE /{slug}/members/{id}`, `DELETE /{slug}/profiles/{name}`,
  `DELETE /{slug}/contacts/{id}`, `DELETE /{slug}/links`,
  `DELETE /{slug}/schedule` — every one of them removes or detaches. Archive is
  not a write-lock on the record, it is a stop on *growth*.
- `POST /{slug}/members`, `/profiles`, `/contacts`, `/links` — bookkeeping on a
  shelved record (fixing a wrong contact, attaching the file someone forgot).
  Same class as `PATCH /{slug}`. If you disagree, gate them too — but then say
  so in §13, because that is a policy change, not a bug fix.

`POST /{slug}/archive` and `DELETE /{slug}` must not call the helper: archive
has its own already-archived refusal (step 1), and hard delete *requires*
`archived` to be true.

The CLI needs no change: every verb reaches these same routes
(`hermes_cli/projects_cli.py`), so it inherits the gate.

#### Step 3 — the run page learns the project is archived

`app/projects/[slug]/runs/[runNo]/page.tsx` fetches only the run (line 40,
`client.projectRun(slug, runNoInt)`), so `RunView` cannot know. It already holds
an `apiClientForRequest()` — fetch the project alongside the run and pass the
flag down:

```ts
const [run, project] = await Promise.all([
  client.projectRun(slug, runNoInt),
  client.project(slug),   // ProjectDetail extends Project — it carries `archived`
]);
...
<RunView slug={slug} run={run} archived={project.archived} />
```

Keep the existing 404 → `notFound()` behaviour, and treat a failed *project*
fetch as non-fatal (default `archived = false`) — a run page that renders
without the flag is better than one that errors, and the router refuses the
write anyway.

In `RunView` (`components/projects/RunView.tsx:42`) add `archived = false` to
the props and hide, when archived: **Continue** (191), **Save retro** (328),
the score control (341–380) and **Repeat this run** (the `${slugPath}/runs`
button at ~206). Leave **Cancel** visible — step 1/2 keep it working. Render
the same one-liner the panels use, so the copy matches
`GuidancePanel.tsx:219`:

> This project is archived — restore it (⋯) to continue or score this run.

#### Step 4 — the delete gate counts what the delete route counts (U8)

`_card_rollup_sync` (`projects_api.py:268`) passes `principal=principal`, so a
non-owner's count is visibility-filtered; the delete route (1090) counts with
no principal, on purpose. Add a third number from the same query path — an
unfiltered `COUNT(*)`, not a listing:

```python
rollup["total_all_principals"] = bconn.execute(
    "SELECT COUNT(*) AS n FROM tasks WHERE project_id = ?", (project_id,)
).fetchone()["n"]
```

Then have `ProjectLifecycleMenu`'s `deleteEligible` read
`card_rollup.total_all_principals` instead of `total_with_archived`. A count
leaks no title, assignee or column. Keep `total_with_archived` — the payload is
public API to the menu and the tests pin it.

#### Step 5 — tests and the doc, in the same PR

- **Extend, don't add**:
  `tests/hermes_cli/test_projects_api_lifecycle.py::test_archived_project_refuses_every_mutating_route`
  is already a table of `(method, url, body)` — grow it to all 12 routes from
  step 2. The table *being* the enumeration is what stops route 13 arriving
  ungated. Keep its tail assertions (no run, no schedule, no stray directive)
  and add: the run's `retro` and `score_user` are still `None`, and no card was
  created.
- **New, in the same file**:
  `test_archive_refuses_while_a_run_is_open` — start a run, drive it to
  `waiting`, archive → 409 naming the run; cancel it, archive → 200.
  `test_cancel_still_works_on_an_archived_project` — the escape hatch, pinned.
- **New**: `test_delete_gate_counts_cards_it_cannot_see` — two cards, one
  private to another user; the detail payload's `total_all_principals` is 2 for
  a lead who sees one, and `DELETE` refuses consistently.
- **agent-home**: in `RunView`'s test file (create it — there is none today),
  assert that an archived project's run renders no Continue / Save retro /
  score control and still renders Cancel, and that a live one renders them all.
- **Docs**: FG-32 §13 and §16 "Lifecycle" already assert the general rule, so
  step 2 makes them true rather than needing a rewrite — but add the
  deliberately-open list to §13 in one sentence, and tick U7/U8 in the Block 4d
  checklist above with a note on how each landed (the house style in Blocks
  1–4c).

**If you choose the other branch of U7** — gate only the five and keep the rest
open — then steps 1 and 3 still apply (`continue` resuming a shelved project is
a defect under any policy, and the run page must match), step 2 shrinks to
`continue` alone, and §13/§16 must be narrowed to enumerate exactly what a
shelved project still accepts. Do not leave the wording general with a partial
gate.

---

## Review of Block 4d — findings U9–U12

Read at `develop` `5a2c3a58f` (#310, implementation commit `59d39ff50`).
Verified here, not taken on trust: `tests/hermes_cli/test_projects_api_lifecycle.py`
22 passed, `-k project` across `tests/hermes_cli` 313 passed / 1 skipped, and
`RunView.test.tsx` + `ProjectLifecycleMenu.test.tsx` 13 passed.

**What Block 4d actually closed.** All four plan steps landed as written:
`_archive_sync` refuses a `running`/`waiting` run and names it;
`_refuse_if_archived` is now called from all twelve growing routes with the
deliberately-open list written into its docstring; the run page fetches the
project alongside the run and `RunView` hides Continue / Repeat / Save retro /
the score control while Cancel stays; `_card_rollup_sync` publishes
`total_all_principals` and `ProjectLifecycleMenu.deleteEligible` reads it. U7
and U8 are closed **at the Projects router**. U9 is the same invariant leaking
through a door that is not the Projects router.

### U9 — cards still reach a shelved project through two non-Projects doors (medium)

`POST /{slug}/cards` is gated, but `tasks.project_id` is writable from outside
`projects_api.py`, and neither writer looks at `archived`:

1. **`POST /api/registry/todos/{todo_id}/promote`** —
   `hermes_cli/todos_api.py:526`, resolving the project at 558–572 and calling
   `kanban_db.create_task(..., project_id=project.id)` at 588 plus
   `projects_db.add_project_link(..., kind="todo")` at 613–622. It resolves the
   project by slug and never asks whether it is archived, so a human promoting
   a to-do into a shelved project gets a `triage` card on the board *and* a new
   `project_links` row — the archived record grows twice over.
2. **`hermes kanban create --project <slug>`** — `hermes_cli/kanban.py:1338`
   passes `project_id` straight into the same `create_task`.

Both land in `kanban_db.create_task` (`kanban_db.py:2442`), whose project
resolution (2523–2542) is explicitly written to *silently null an unresolvable
project id* — and to accept every project it can resolve. An archived project
resolves fine.

**Why it matters, not just wording.** This is the exact orphan class hard
delete's card blocker exists to prevent: a project that has been archived with
zero cards (and is therefore delete-eligible) can silently gain cards, and the
kanban store has no FK back to `projects`, so nothing else notices. It also
re-opens the U7 promise one route below the one Block 4d fixed: FG-32 §13's
"a shelved project does not run and does not learn" is now true of the router
and false of the system.

**The fix — one gate, at the writer, not two at the callers.** Put the refusal
where the project is already resolved, in `create_task`'s project branch
(`kanban_db.py:2530`): if the resolved project is archived, raise
`ValueError(f"project {project_obj.slug} is archived — restore it before adding
a card")` rather than nulling the id. `create_task` already raises `ValueError`
for bad input, so both callers surface it (`todos_api.promote_todo` should map
it to **409**, matching the router's archived refusal, instead of the current
generic 500 at 604–607). Do **not** null the `project_id` and keep the card:
that produces exactly the dangling triage card the promote route already deletes
at 597–601.

Also gate the link half of promote: `POST /{slug}/links` is on the
deliberately-open list as *bookkeeping*, but promote's link write accompanies a
card creation, so if the card is refused the link must not be written (it is
written after the card today, so raising in `create_task` is enough).

**The tests.**
- `tests/hermes_cli/test_todos_promote.py` (where the promote contracts already
  live): promote a to-do into an archived project → 409, the project's
  `total_all_principals` is unchanged, and no `project_links` row appears.
- `tests/hermes_cli/test_kanban_db.py`: `create_task(project_id=<archived>)`
  raises, and `create_task(project_id=<active>)` still returns a card carrying
  the id (the regression guard for the "silently null" behaviour above).

### U10 — the archive open-run scan can miss an old open run (low)

`projects_api.py:1012–1015`:

```python
open_runs = [
    r
    for r in projects_db.list_project_runs(conn, fresh.id, limit=50)
    if r["status"] in ("running", "waiting")
]
```

`list_project_runs` (`projects_db.py:2373`) is `ORDER BY run_no DESC LIMIT ?`,
so the precondition only inspects the newest 50 runs. A standing project — the
cadence this feature exists for — passes 50 runs in a year of weekly cadence
plus retries, and a run stuck at a checkpoint months ago is then invisible to
the gate: archive succeeds and leaves precisely the resumable-run state U7's
step 1 was added to prevent (`continue` is gated now, so the harm is a lie in
the record rather than work restarting — which is why this is low, not medium).

**The fix.** Ask the question in SQL instead of paging: add
`projects_db.list_open_project_runs(conn, project_id)` —
`SELECT run_no, status FROM project_runs WHERE project_id = ? AND status IN
('running','waiting') ORDER BY run_no` — and have `_archive_sync` use it. No
limit, no ordering assumption, and the 409 message keeps naming every held run.

**The test.** Seed 51 `done` runs plus one `waiting` run at `run_no` 1, archive
→ 409 naming run 1 (this fails against the current code).

### U11 — the two Block 4d surfaces with no test (low)

`test_delete_gate_counts_cards_it_cannot_see` pins the *payload*, but nothing
pins the *consumer*: `ProjectLifecycleMenu.test.tsx` never sets
`total_all_principals`, so the U8 fix's UI half rides on the `??` fallback
chain and a future rename of the field silently restores the old behaviour.
Likewise nothing covers the run page's own change — that it fetches the project
alongside the run, and that a *failed* project fetch renders the run unflagged
rather than erroring the page (the deliberate choice in #310's step 3).

**The fix.** Two cases in `ProjectLifecycleMenu.test.tsx`: an archived project
with `total_all_principals: 2` and `total_with_archived: 0` offers no Delete;
the same with both `0` offers it. And a `page.test.tsx` (or a `RunView` case
driven by the page's loader) for the archived / fetch-failed pair.

### U12 — "archived does not grow" is still not literally true (low, or a doc fix)

`POST /{slug}/links` (`projects_api.py:1546`) is deliberately open as
bookkeeping, but links are how §1.1's samples, references, files, memories and
conversation histories attach to a project. So a shelved project can still gain
references and conversation history — a *record* write, not a run.

Pick one and stop the drift: either gate `POST /{slug}/links` too (leaving the
`DELETE` open), or say in FG-32 §13 that archive stops *execution and learning*
and explicitly permits record bookkeeping — naming links, members, profiles,
contacts and `PATCH /{slug}`. The helper docstring already lists them; §13 is
what disagrees.

**Block 4e — close U9, and the three lows with it.**

**All four landed in Block 4e (#312, `3b50c6224`)** — the gate moved to the writer
(`kanban_db.create_task`), promote maps the refusal to the router's own 409,
archive's open-run scan is unpaged, the two untested Block 4d surfaces have
tests, and §13 says what archive truly stops.

- [x] **U9** Refuse an archived project in `kanban_db.create_task`'s project
      branch (`kanban_db.py:2530`) so `todos_api.promote_todo` and
      `hermes kanban create --project` cannot grow a shelved project's board;
      map the `ValueError` to 409 in the promote route. Tests: promote →
      409 + no card + no link; `create_task` raises on archived, still works on
      active. *(Block 4e: the gate sits in `create_task`'s project branch;
      promote answers 409 and the link row never lands
      (`test_todos_promote.py::TestPromoteRefusesArchivedProject`), the CLI
      prints the refusal instead of a traceback, and
      `test_kanban_db.py::test_create_task_refuses_an_archived_project` +
      `test_create_task_keeps_an_active_project_link` pin the writer both
      ways.)*
- [x] **U10** Replace the `limit=50` scan in `_archive_sync` with a
      `list_open_project_runs` SQL query. Test: 51 `done` runs and a `waiting`
      run 1 → archive refuses. *(Block 4e:
      `projects_db.list_open_project_runs` — SQL, unpaged, ordered by
      `run_no`; `test_archive_refuses_an_old_open_run_beyond_the_page_window`
      seeds run 1 `waiting` under 51 `done` runs and fails against the old
      scan.)*
- [x] **U11** Test the U8 consumer (`ProjectLifecycleMenu` reading
      `total_all_principals`) and the run page's project fetch, including the
      fetch-failed path. *(Block 4e: two `ProjectLifecycleMenu` cases pin the
      consumer on `total_all_principals` (hidden cards block Delete, zero
      across all principals offers it); the run page's own `page.test.tsx`
      pins archived / live / fetch-failed-renders-unflagged / 404.)*
- [x] **U12** Decide links: gate `POST /{slug}/links`, or narrow FG-32 §13 to
      "stops execution and learning; record bookkeeping stays open" with the
      permitted list named. *(Block 4e decided the narrowing: links stay open
      as record bookkeeping — §13 now says archive stops execution and
      learning, names links as how samples, references, files, memories and
      conversation histories attach, and lists the open bookkeeping class.)*

---

## Review of Block 4e — findings U13–U15

Read at `develop` `31351551a` (#312, implementation commit `3b50c6224`).
Verified here, not taken on trust: the three touched Python test files pass
(259), the run-page + `components/projects` vitest files pass (77),
`tsc --noEmit` and `ruff` are clean. The 22 failures in a wide
`-k "project or kanban or todo"` sweep are **pre-existing** — the identical set
fails at `4c40a5e45` (pre-#312), so they are cross-file pollution in the kanban
suites, not a regression from this block.

**What Block 4e actually closed.** U9's gate went in at the writer, where it
covers every caller and not just the two the finding named: besides
`todos_api.promote_todo` and `hermes kanban create --project`, the same
`create_task` refusal now protects `kanban_swarm` (4 call sites),
`tools/kanban_tools.py:938` (which already catches `ValueError` and answers a
tool error) and `projects_run.py:369`. U10 is SQL and unpaged. U11's two
surfaces have behaviour tests — the menu cases drive `total_all_principals`
2 → no Delete / 0 → Delete, and `page.test.tsx` runs the page's real loader for
archived / live / fetch-failed / run-404. U12 chose the narrowing and wrote it
into **both** FG-32 §13 and §16, naming links as record bookkeeping.

Three residuals, all low. None of them reopens an invariant; U13 is the only
one with a runtime-visible effect.

### U13 — the `ValueError` catch is wider than the refusal it maps (low)

`hermes_cli/todos_api.py:599–604` catches `ValueError` around the *whole*
card-creation block and answers **409**:

```python
except ValueError as exc:
    raise HTTPException(status_code=409, detail=str(exc)) from exc
```

`create_task` raises `ValueError` for a family of plain input errors that have
nothing to do with archiving — `initial_status must be one of …`,
`workspace_kind must be one of …`, `branch_name is only valid for worktree
workspaces`, and the skills-name validation below them. Those now return a
`409` whose detail claims a conflict, and — because the new clause sits *above*
the general `except Exception` — they also lose the
`logger.warning("todos: promote card creation failed …")` line they used to
emit. `hermes_cli/kanban.py:1350` has the same width: any `create_task`
`ValueError` prints `kanban create: …` and exits 2, so a genuine argument
error is now reported in the same voice as the archive refusal.

**The fix.** Give the refusal its own identity instead of overloading the
built-in. Define one exception beside the writer — e.g.
`class ArchivedProjectError(ValueError)` in `kanban_db.py` (subclassing
`ValueError` keeps every existing caller's behaviour, including
`kanban_tools`' catch) — raise it in the project branch, and narrow both
handlers to it. `todos_api` then keeps its `except Exception` path (log +
500-family) for real input errors, and `kanban.py`'s message stays truthful.

**The tests.** In `tests/hermes_cli/test_todos_promote.py`, alongside the
archived case: promote with a `create_task` input error → **not** 409 (and the
warning is logged). In `tests/hermes_cli/test_kanban_cli.py`, an archived
project prints the refusal on stderr with rc 2, and a bad `--initial-status`
still reports its own error.

### U14 — `getattr` on a declared field (low, code quality)

`hermes_cli/kanban_db.py:2537`:

```python
elif getattr(project_obj, "archived", 0):
```

`projects_db.Project` declares `archived: bool = False`
(`projects_db.py:631`), and `from_row` coerces it with
`bool(_row_get(row, "archived", 0))` (714), so the attribute always exists on
anything `get_project` returns. `AGENTS.md` names `getattr` as a lazy typing
escape not to reach for; the default `0` also silently reads falsy if the field
is ever renamed, which is exactly the failure mode a plain attribute access
would surface loudly.

**The fix.** `elif project_obj.archived:`. Nothing else changes — the existing
`test_create_task_refuses_an_archived_project` /
`test_create_task_keeps_an_active_project_link` pair already pins both sides.

### U15 — the security boundary is pinned by a mock at the route (low)

`tests/hermes_cli/test_kanban_db.py` tests the writer for real (real project,
real archive, real store, and it asserts no card landed) — that is the right
shape. The route test does not: it patches `kanban_db.create_task` with a
`side_effect` `ValueError`, so it pins the *mapping* to 409 and that
`add_project_link` / `set_stage` were not called, but nothing exercises
`POST /api/registry/todos/{id}/promote` against an actually-archived project.
Per `AGENTS.md` ("E2E validation, not just green unit mocks" — security
boundaries specifically), the archived gate wants one real-path test. And
`hermes kanban create --project <archived>` has **no** test at all:
`test_kanban_cli.py` contains zero occurrences of `archived`.

**The fix.** Add one real-path case per door, keeping the mocked mapping test
(it is cheap and pins the status code):

- `test_todos_promote.py`: create a project, archive it through
  `projects_db.archive_project`, promote a real to-do into it → 409; then
  assert against the stores, not the mocks — no card on the board, no
  `project_links` row of `kind="todo"`, and the to-do still at its original
  stage.
- `test_kanban_cli.py`: `hermes kanban create --project <archived-slug>` → rc 2,
  the refusal on stderr, and no task row created.

**Block 4f — finish the writer-level gate.**

**All three landed in Block 4f (this PR)** — the refusal has its own
identity (`ArchivedProjectError`), the two handlers are narrowed to it,
the gate reads the declared `archived` field, and the boundary has
real-path tests on both doors.

- [x] **U13** Introduce `ArchivedProjectError(ValueError)` at the writer and
      narrow the `todos_api` (409) and `kanban.py` (rc 2) handlers to it, so
      ordinary `create_task` input errors keep their old status and their log
      line. Tests: a `create_task` input error through promote is not a 409;
      the CLI reports an argument error in its own voice. *(Block 4f:
      `kanban_db.ArchivedProjectError(ValueError)` — the subclass keeps
      `kanban_tools`' existing catch; promote answers 409 for the refusal
      alone and the input-error path gets its log line and the generic 500
      back (`TestPromoteWriterGateWidth`); the CLI splits the archived voice
      from an `invalid arguments:` voice, and the usage-error case pins the
      argparse report.)*
- [x] **U14** Replace `getattr(project_obj, "archived", 0)` with
      `project_obj.archived` (`kanban_db.py:2537`) — `Project.archived` is a
      declared, coerced field. *(Block 4f: done — the existing
      archived/active writer pair pins both sides.)*
- [x] **U15** Add the two real-path tests the boundary is missing: promote into
      a genuinely archived project (asserting the board and `project_links`
      are untouched and the to-do did not move), and
      `hermes kanban create --project <archived>` → rc 2 with no task row.
      *(Block 4f: `TestPromoteRealPathArchivedGate` runs the real route
      against a genuinely archived project — real writer, real board, real
      `project_links`, only the Supabase-only to-do store mocked;
      `test_run_slash_create_refuses_an_archived_project` drives the real
      CLI end to end and finds no task row.)*
