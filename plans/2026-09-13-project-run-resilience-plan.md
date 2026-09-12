# Plan: Project runs must survive a restart, refill their own capacity, and be diagnosable when stuck

**Status:** proposed (not yet implemented) — see companion status doc
`plans/2026-09-13-project-run-resilience-status.md`.
**Trigger:** found while investigating why project `ai-professsional-builder-course-ppt`'s
run #1 (`run_91b11d845e73`) sat in `status: running` for 13+ hours on the Hetzner
production box with zero cards ever started and `hermes projects doctor` reporting
nothing wrong.
**Feature this extends:** FG-32 (`docs/design/master-plan/feature-groups/FG-32-projects-durable-record.md`).
This is a new defect, not one of the 21 already tracked in FG-32 §20.2 — it belongs
next to that list once fixed, so §20.2 gets a pointer to this doc as part of the work.

## 1. What actually happened (root cause, confirmed on the box)

1. `hermes projects run` was triggered manually at `04:46:58 UTC`. `start_run()`
   (`hermes_cli/projects_run.py`) instantiated the playbook's 7 steps as cards in
   `triage`, then called `promote_run_cards()` **once**. Because the project's
   `max_in_progress = 1`, that call promoted exactly one card
   (`triage → todo`) and returned — by design (§4 / FG-32 line 555: *"before
   promoting step cards, count this project's cards in running+ready; promote at
   most up to the cap"*).
2. Nothing ever calls `promote_run_cards()` again for this run. It is invoked
   from exactly two places in the whole codebase: `start_run()` and
   `continue_run()` (the human "continue past a checkpoint" action). There is no
   hook that re-invokes it when a card finishes, fails, or is blocked and a
   `max_in_progress` slot frees up. **A run can only ever promote its first
   batch of cards — after that it is permanently stalled unless a human calls
   `continue`,** even with zero restarts involved.
3. Independently, at `06:03:2x UTC` (~1h16m into the run) every `hermes-*`
   systemd service on the box — including `hermes-gateway`, which owns the
   embedded kanban dispatcher tick (`gateway/kanban_watchers.py`
   `_kanban_dispatcher_watcher`) — was bulk-restarted (no matching
   `/opt/data/deploy-*.log` entry, so likely a manual restart from the
   colocated `aicoder` session, not the reviewed deploy tool). This is
   unrelated to the run's own state (which lives in SQLite, not memory) but it
   is the kind of event a resilient design has to assume will happen mid-run.
4. Result: the run's `status` column has stayed `"running"` for 13+ hours,
   `session_id` is `NULL` (no inline session was ever spawned — this project's
   playbook has no inline steps), one card sits in `todo` unable to reach
   `ready` (its dependency card never left `triage`), and six cards sit
   untouched in `triage`. Nothing is watching this. `hermes projects doctor`
   (`hermes_cli/projects_schedule.py` `doctor_findings`) has no code path that
   looks at a `running` run's age or lack of card activity — its liveness
   checks (`stalled_repeatable`, `one_off_overdue`) only look at *schedule*
   staleness (repeatable cadence) or `due_at` (one-off cadence), never at an
   in-flight run that has gone dark.

So there are three independent gaps, and all three need fixing — patching only
the restart-survival angle leaves runs that stall with zero restarts involved
(gap A alone would have stalled this run at ~1 card even if the box never
restarted):

- **Gap A — no capacity refill.** `promote_run_cards()` is a one-shot call, not
  re-driven by card completion.
- **Gap B — no crash/restart recovery.** A `running` run has no liveness marker
  and nothing reconciles it after the process that would have driven it
  restarts.
- **Gap C — doctor/health is blind to a stuck run.** `doctor_findings` /
  `derive_health` (`hermes_cli/projects_schedule.py`) never look at run
  liveness, only at schedule/due-date staleness.

## 2. Design constraints (from `AGENTS.md` and FG-32 itself — read before implementing)

- **Never patch the shared dispatcher.** FG-32 is explicit: `max_in_progress`
  is "enforced in the project's own promotion step, never by patching the
  shared dispatcher" (line 1645, "decision 17" pattern already applied for
  the whole feature). The fix must stay a Projects-layer concern that *calls*
  into the existing dispatcher/kanban primitives, not a change to
  `kanban_db.dispatch_once`'s general-purpose scheduling semantics — the
  kanban board is shared with non-Projects boards.
- **Footprint ladder (rung 1): extend existing code.** No new core tool, no new
  service. The refill/reconciliation logic is a function call added to
  code paths and loops that already exist (`kanban_db.complete_task` /
  `block_task` completion path, and the gateway's existing
  `_kanban_dispatcher_watcher` tick loop).
- **Config, not env vars.** Any new threshold (stall window, reconciliation
  interval) is a `projects:` key in `config.yaml`, following the existing
  `projects_runtime_config()` pattern in `hermes_cli/projects_run.py`
  (`max_skills`, `guidance_max_directives`, etc.) — never a new `HERMES_*`
  env var.
- **Side-effect / single-writer rules.** The board already has a
  `_dispatch_tick_lock` (non-blocking, board-scoped) to stop two dispatchers
  writing concurrently. Any reconciliation pass that touches the board must
  either run inside an existing locked tick or take the same lock — never
  write to `tasks`/`project_runs` from two processes unguarded.
- **Fail open on observability, fail loud on the run record.** Cost/health
  reads must never crash a run (existing pattern throughout
  `projects_run.py`); but the run record itself (`project_runs.status`) must
  never silently claim `running` past the point it's actually able to
  progress — that's exactly today's bug.

## 3. Fix design

### 3.1 Gap A — refill capacity when a run-linked card frees a slot

Add `hermes_cli/projects_run.py::reconcile_run(pconn, bconn, *, project, run) -> dict`:
- Computes `room` the same way `promote_run_cards()` already does
  (`_project_in_flight` vs `project.max_in_progress`).
- If `room > 0` and the run has un-promoted `triage` cards linked via
  `projects_db.get_run_cards()`, calls `promote_run_cards()` again (idempotent —
  it already no-ops when nothing is eligible).
- If, after promotion, there is nothing left to promote, nothing running, and
  nothing ready for this run's cards, and the run's own outputs are not yet
  delivered → this is the "genuinely done" vs "genuinely stuck" fork handled
  in §3.2, not here (this function only ever tops up capacity; it never closes
  a run).
- Returns the same shape `promote_run_cards()` returns (`promoted: [...]`) so
  it can be logged/tested identically.

**Call site:** a task's terminal transition is exactly where "room just freed
up" is known. `hermes_cli/kanban_db.py` already special-cases `project_id` in
several places (e.g. worktree/branch anchoring). Add one more: after
`complete_task()` and `block_task()` (and the failure-limit-exhausted path)
commit a status change on a task with a non-null `project_id`, do a **lazy,
best-effort** call:

```python
if task_row["project_id"]:
    try:
        from hermes_cli import projects_reconcile
        projects_reconcile.on_card_settled(task_row["project_id"], conn)
    except Exception:
        log.warning("projects: reconcile-on-settle failed for %s", task_id, exc_info=True)
```

`hermes_cli/projects_reconcile.py` is a **new, thin module** (not a new tool,
not a new service — just where this cross-cutting glue lives so
`kanban_db.py` doesn't import `projects_run.py` directly and create a layering
cycle, matching the existing lazy-import convention used throughout
`projects_run.py`). It opens the `projects.db` connection, finds the run(s)
this card belongs to via `projects_db.get_run_cards`/a new
`get_card_run(bconn, task_id)` lookup, and calls `reconcile_run()` for each
`running` run. Failures are logged and swallowed — a broken reconciliation
must never break the kanban card transition that triggered it.

This closes Gap A on its own, independent of restarts: complete a card, and
the next one in the queue is promoted immediately, the same tick.

### 3.2 Gap B — recover (or fail loudly) after a restart

Add `hermes_cli/projects_reconcile.py::reconcile_all_open_runs(now=None) -> list[dict]`:
- Lists every project run with `status == "running"` across all readable
  projects (`projects_db.list_projects` + `projects_db.get_project_runs`).
- For each: calls `reconcile_run()` (§3.1) first — a restart is just a special
  case of "something didn't call promote again," so the same refill logic
  is the first line of recovery, no separate code path.
- Then applies a **staleness verdict**, using a new config knob
  `projects.run_stall_seconds` (default `7200`, i.e. 2 hours — long enough to
  never fire on a legitimately long-running inline session, short enough to
  catch this class of bug same-day):
  - `last_activity_at` = `max(run.started_at, latest card event timestamp for
    any card linked to the run, run.session last-activity if inline)`.
  - If `now - last_activity_at > run_stall_seconds` **and** reconciliation just
    now found nothing to promote/resume (no card in `ready`/`running`, no live
    inline session) → the run is dead, not merely slow. Close it:
    `projects_run.close_run(pconn, run=run, status="failed", outcome="stalled",
    error="no worker or session progressed this run for over "
    "<run_stall_seconds>s — likely orphaned by a process restart")`, and raise
    the existing FG-10 approval/notification path (`raise_approval`, already
    used for checkpoints/budget) so the owner is told, not left to notice a
    silently-dead run on the list page.
  - If it found something to promote (Gap A's refill fired) → leave it
    `running`; it just resumed.

**Call site:** reuse the loop that already exists instead of adding a new
one. `gateway/kanban_watchers.py::_kanban_dispatcher_watcher` already ticks
`kanban_db.dispatch_once` on an interval (`dispatch_interval_seconds`). Add a
sibling low-frequency call — every Nth tick (config
`projects.reconcile_interval_seconds`, default `300`) and once unconditionally
a few seconds after gateway startup (covers exactly the "process just
restarted mid-run" case) — to `projects_reconcile.reconcile_all_open_runs()`.
This is additive to the existing watcher, not a change to
`kanban_db.dispatch_once`'s semantics, so it does not touch the "never patch
the shared dispatcher" boundary: it calls the Projects layer, which calls the
already-public `promote_run_cards`/kanban primitives the same way a human's
`continue` action does today.

This closes Gap B: whether the process died before ever spawning a worker
(this incident) or died mid-flight, the next reconciliation pass either
resumes the run for real or fails it loudly within `run_stall_seconds`.

### 3.3 Gap C — doctor/health must see a stuck run

Extend `hermes_cli/projects_schedule.py`:
- `doctor_findings()`: add a new code `run_stalled` (checked for **every**
  cadence, not just `repeatable` — today's `stalled_repeatable` only covers
  repeatable projects; a stuck `one_off` run is invisible). Reuses the same
  `last_activity_at` computation as §3.2 (factor it into a shared helper,
  e.g. `hermes_cli/projects_reconcile.py::run_last_activity_at(pconn, bconn,
  run)`, imported by both doctor and the reconciler so they can never
  disagree about what "stale" means).
  ```python
  open_runs = [r for r in runs if r.get("status") == "running"]
  for r in open_runs:
      last = run_last_activity_at(conn, board_conn, r)
      if last and now - last > cfg["run_stall_seconds"]:
          _add("run_stalled",
               f"run {r['run_no']} has shown no card/session activity for "
               f"{(now - last)//3600}h — likely orphaned")
  ```
  Severity map addition: `"run_stalled": "attention"` (escalate to
  `"stalled"` only past a second, longer threshold — e.g. `4 * run_stall_seconds`
  — via `derive_health()`, matching the existing "stalled outranks attention"
  precedent).
- `derive_health()`: add the same open-run staleness check to the `attention`
  block (§9.2 table already lists "a run is `waiting` on an ask or budget" —
  add "a run is `running` with no activity for `run_stall_seconds`" right next
  to it), so the list page's health chip and the Attention filter catch this
  without a human running `doctor` explicitly.

This closes Gap C: even if reconciliation (Gap B) is somehow delayed or
disabled, the project's health/doctor surface will say so within one read,
instead of showing `[ok]`/`[active]` on a project whose run has been dead for
half a day.

## 4. Config additions (`config.yaml`, read via `projects_runtime_config()`)

```yaml
projects:
  run_stall_seconds: 7200          # gap B/C threshold — 2h of no card/session activity
  reconcile_interval_seconds: 300  # gap B — how often the gateway reconciles open runs
```

Both fail-open to the defaults above if unset or malformed (same
`_int_or()` helper already used for `max_skills` etc.).

## 5. Tests to add

- `tests/hermes_cli/test_projects_run.py` (extend): a run with
  `max_in_progress=1` and two independent (non-dependent) triage cards;
  complete the first card via `kanban_db.complete_task`; assert the second is
  promoted to `todo` with **no** call to `continue_run` — proves Gap A's fix
  without needing the reconciler.
- New `tests/hermes_cli/test_projects_reconcile.py`:
  - `reconcile_run()` promotes when room frees and no-ops when it doesn't
    (mirrors `test_projects_run.py::promote_run_cards` cases).
  - `reconcile_all_open_runs()` on a run whose `started_at` is older than
    `run_stall_seconds` with zero card/session activity → asserts the run is
    closed `failed`/`stalled` and an approval was raised (inject a fake
    `raise_approval`/store as the existing tests already do for
    `budget_gate`).
  - Same function on a run that still has promotable capacity → asserts it
    resumes (`status` stays `running`, `promoted` non-empty) — this is the
    regression test for the exact incident (a run orphaned by a restart before
    any card started).
- `tests/hermes_cli/test_projects_schedule.py` (or wherever
  `doctor_findings`/`derive_health` are currently tested — confirm path before
  writing): add `run_stalled` cases for `one_off`, `repeatable`, and
  `standing` cadence, and a `derive_health` case asserting `attention` for a
  stale running run.
- `tests/gateway/test_kanban_watchers.py` (confirm exact file first): assert
  `_kanban_dispatcher_watcher` calls `reconcile_all_open_runs` once shortly
  after start and every `reconcile_interval_seconds` thereafter (use the
  existing fake-clock/interval test pattern in that file, mirroring how the
  dispatch-tick interval is already tested).
- E2E-ish check per `AGENTS.md` ("E2E validation, not just green unit mocks"
  for anything touching resolution chains/config propagation): a test that
  starts a real run against a temp `HERMES_HOME` + temp kanban board with
  `max_in_progress=1` and a 3-step linear playbook, drives it to completion by
  repeatedly calling `complete_task` + relying on the new refill hook (no
  manual `continue_run` calls), and asserts all 3 cards end `done` and the run
  auto-closes — this is the scenario that was silently broken in production.

## 6. Rollout / operational notes

- This is a `fix(...)` PR against real, reproduced production behavior — no
  new core tool, no new `HERMES_*` env var, fits the footprint ladder at rung
  1 (extend existing code) per `AGENTS.md`. Should go through the normal
  `develop` PR flow.
- Once merged and deployed (via the reviewed `/opt/data/deploy-hermes.sh
  develop`, never a bare `systemctl restart`), separately triage the
  already-orphaned production run `run_91b11d845e73` on project
  `ai-professsional-builder-course-ppt` — the new reconciler will pick it up
  on its next tick and either resume or fail it loudly; no manual SQL surgery
  needed once this ships.
- Add a one-line pointer from FG-32 §20.2 to this plan doc once the PR lands,
  so this defect joins the tracked list instead of living only here.

## 7. Work breakdown (suggested PR sequence)

1. `projects_reconcile.py` + `reconcile_run()` (Gap A) + kanban_db call site +
   unit tests. Smallest, highest-value, no new config yet (call it
   unconditionally — it's cheap and idempotent).
2. `reconcile_all_open_runs()` + config knobs + gateway watcher wiring (Gap B)
   + tests, including the restart-recovery regression test.
3. `doctor_findings` / `derive_health` `run_stalled` (Gap C) + tests. Can ship
   independently of/before 1–2 if reviewed faster — it's read-only.
4. FG-32 §20.2 pointer update.
