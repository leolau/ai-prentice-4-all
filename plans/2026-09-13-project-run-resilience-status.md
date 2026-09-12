# Implementation status: Project run resilience (refill + restart recovery + doctor)

Companion to `plans/2026-09-13-project-run-resilience-plan.md`. Update this
file as work lands — one row per gap, per the plan's §7 breakdown.

**Deploy status as of 2026-09-13**: Gap A (#388) is merged AND deployed to
the Hetzner production box (`76d8a0984`). Gap B/C/D (#389) are merged to
`develop` but **deliberately not deployed** — built and verified locally
per an explicit request to not touch production while doing this round of
work. The next production deploy of `develop` will carry them.

| # | Gap | Description | Status | PR / commit | Notes |
|---|-----|-------------|--------|--------------|-------|
| 1 | A | `refill_run_cards()` + `projects_reconcile.on_card_settled()` refill hook (kanban_db → projects_reconcile → projects_run on card settle) | **Done** | [#388](https://github.com/leolau/ai-prentice-4-all/pull/388) | See "Gap A implementation notes" below |
| 2 | B | `reconcile_all_open_runs()` + `run_stall_seconds`/`reconcile_interval_seconds` config + gateway watcher wiring + auto-fail stale runs | **Done** (code merged; **not deployed to production**) | [#389](https://github.com/leolau/ai-prentice-4-all/pull/389) | See "Gap B/C/D implementation notes" below |
| 3 | C | `doctor_findings`/`derive_health` `run_stalled` code (all cadences) | **Done** (code merged; **not deployed to production**) | [#389](https://github.com/leolau/ai-prentice-4-all/pull/389) | Read-only, shipped alongside B/D |
| 4 | — | FG-32 §20.2 pointer to this defect + plan doc | **Not started** | — | Do once #389 is deployed and verified live |
| 5 | — | Tests (unit: refill, reconcile, doctor; gateway watcher interval; E2E full-run-without-manual-continue) | **Done** | [#388](https://github.com/leolau/ai-prentice-4-all/pull/388), [#389](https://github.com/leolau/ai-prentice-4-all/pull/389) | Gap A tests in #388; B/C/D tests in #389 |
| 6 | — | Production cleanup: orphaned run `run_91b11d845e73` on `ai-professsional-builder-course-ppt` | **Done** | — | Manually unstuck 2026-09-12 — see "Gap D" below for why a code fix alone wouldn't have caught this one |
| 7 | D (new) | `promote_run_cards()` picks which `triage` card to promote by **task-ID sort order**, not playbook/dependency order — under a `max_in_progress` cap smaller than the playbook width, it can promote a card whose own dependency was never promoted, deadlocking the run permanently (this is what actually broke the production run, not Gap A) | **Done** (code merged; **not deployed to production**) | [#389](https://github.com/leolau/ai-prentice-4-all/pull/389) | See "Gap D details" below |

## Investigation record (already done, 2026-09-13)

- Confirmed on the Hetzner production box (`188.245.219.105`) via
  `hermes projects list/runs/cards --json`:
  - Run `run_91b11d845e73` (project `ai-professsional-builder-course-ppt`,
    run #1) started `2026-09-12 04:46:58 UTC`, still `status: running` as of
    `2026-09-12 18:37 UTC` (~13.8h), `session_id: null`, 0/7 cards ever
    started.
  - `hermes projects doctor --json` returned `{"items": []}` — confirms Gap C.
  - `systemctl show <unit> -p ActiveEnterTimestamp` for `hermes-gateway`,
    `hermes-dashboard`, `agent-home`, `hermes-embed`, `hermes-email-poller`,
    `hermes-calendar-poller` all show `2026-09-12 06:03:2x/40 UTC` — a
    bulk restart of every `hermes-*` service ~1h16m into the run, with no
    matching `/opt/data/deploy-*.log` entry (not the reviewed deploy tool).
    `last -x` shows the colocated `aicoder` tmux session ending at the same
    timestamp.
- Read `hermes_cli/projects_run.py` end to end: confirmed `promote_run_cards()`
  is called only from `start_run()` and `continue_run()` — no completion hook
  re-invokes it (Gap A, independent of the restart).
- Read `hermes_cli/projects_schedule.py` `doctor_findings()`/`derive_health()`
  end to end: confirmed no code path inspects a `running` run's age or
  activity for any cadence (Gap C).
- Read `gateway/kanban_watchers.py::_kanban_dispatcher_watcher`: confirmed
  this is the existing periodic-tick loop the fix should extend (Gap B call
  site), rather than adding a new service/loop.

## Gap A implementation notes (deviations from the plan's §3.1 sketch)

- `refill_run_cards(pconn, bconn, *, project, run)` landed in
  `hermes_cli/projects_run.py` (not a separate function named
  `reconcile_run` — folded straight into the module `promote_run_cards`
  already lives in, since it's a thin wrapper around it, mirroring
  `continue_run()`'s own playbook/held setup almost verbatim).
- The completion-side hook is `hermes_cli/projects_reconcile.py`
  (`on_card_settled(bconn, task_id)`), called from a new
  `hermes_cli/kanban_db.py::_reconcile_project_run_capacity(conn, task_id)`
  helper — added right after each of the 3 existing
  `_fire_kanban_lifecycle_hook("kanban_task_completed"/"kanban_task_blocked",
  ...)` call sites (`complete_task`, and both exits of `block_task`:
  the `dependency`-kind early return and the shared blocked/triage-loop
  path). This covers every place a run-linked card leaves
  `running`/`ready` — confirmed by reading all `_fire_kanban_lifecycle_hook`
  call sites rather than assuming.
- Deliberately **not** wired through the plugin `invoke_hook`/`register_hook`
  mechanism in `hermes_cli/plugins.py` — that system is for third-party
  plugins reaching into core (see `website/docs/guides/build-a-hermes-plugin.md`);
  no other core-internal consumer uses it today, and using it here would
  invert that layering. Direct lazy-imported function call instead, matching
  the existing pattern right next to it (`_fire_kanban_lifecycle_hook` itself
  lazy-imports `hermes_cli.plugins`).
- `projects_db.get_run_card_by_task(conn, task_id)` added as the reverse
  lookup (`project_run_cards` has no `task_id` index by design — a full
  table isn't expected to need one at this scale, matches the existing
  `get_run_cards` query style).
- Tests added directly to `tests/hermes_cli/test_projects_run.py` (no new
  test file) in the existing §4/`max_in_progress` section:
  `test_completing_a_card_auto_promotes_the_next_one` (the real
  regression — exercises `kanban_db.complete_task()` end to end, the
  actual production call path, no mocking of the promotion layer) and
  `test_refill_run_cards_is_a_noop_off_a_running_run` (never forces a held
  checkpoint open; no-ops on a closed run).
  - Discovered along the way: `project_run_cards` rows come back from
    `get_run_cards()` sorted by `task_id` (its primary key), not playbook
    order — harmless for steps with real `depends_on` edges (those still
    gate correctly) but means "first card promoted" among mutually
    independent steps is arbitrary. Not in scope for this fix; the test
    was written to not assume a specific step is promoted first.
- Verified no regressions: built a throwaway local venv (Python 3.12,
  `pip install -e .`), ran `tests/hermes_cli/test_projects_run.py` (39
  passed) and the full `test_kanban_*.py` + `test_projects_*.py` sweep
  (973 passed, 25 failed) — then confirmed via `git stash` that the exact
  same 25 tests fail identically on unmodified `develop` (pre-existing
  environment gaps, e.g. a subprocess CLI test needing `asyncpg`, unrelated
  to this change).
- Shipped as its own PR ([#388](https://github.com/leolau/ai-prentice-4-all/pull/388)),
  separate from this plan's docs-only PR ([#387](https://github.com/leolau/ai-prentice-4-all/pull/387)),
  since #387 was explicitly "docs only, no behavior change."

## Gap D details (found 2026-09-13, while manually recovering the production run)

Root-causing the still-stuck production run after Gap A shipped revealed a
**second, independent bug** in `promote_run_cards()`
(`hermes_cli/projects_run.py`) — the actual reason this specific run never
moved:

- `projects_db.get_run_cards()` returns a run's linked cards via
  `SELECT * FROM project_run_cards WHERE run_id = ?` with no `ORDER BY`.
  SQLite returns these in primary-key (`task_id`) order, which is random
  relative to the playbook's step order.
- `promote_run_cards()` iterates that list and promotes the first `N` (up to
  `room`, from `max_in_progress`) cards that are still `status == 'triage'`
  — it does **not** check whether a card's own `depends_on` chain has
  actually been promoted/completed. Dependency gating only happens later,
  at the `todo → ready` transition inside `recompute_ready()`.
- Net effect: with `max_in_progress=1` and a playbook wider than 1 step deep,
  the *first* card promoted can be an arbitrary one — including a card whose
  dependency was never itself promoted. That card then sits in `todo`
  forever (can't reach `ready`, dependency unsatisfied), while the actual
  dependency-free root step never gets a turn, because the run's one
  `max_in_progress` slot is already "spent" on the wrong card.
- This is exactly what happened to `run_91b11d845e73`: `draft-lab-prompts`
  (depends on `draft-general-style-prompt`) got promoted instead of the
  true root `setup-canva-presentation` (no dependencies) — permanently
  deadlocking the run regardless of Gap A/B/C.
- **Not fixed by Gap A**: the refill hook only re-invokes
  `promote_run_cards()` on a *card settling* — with the wrong card stuck in
  `todo` and nothing ever reaching `running`/`ready`, no completion event
  ever fires, so Gap A's hook never gets a chance to run.
- **Manual recovery applied**: directly called
  `kanban_db.specify_triage_task(bconn, "t_ec025ca3", author="leo_owner")`
  (the actual root card) — bypassing the generic `hermes kanban specify`
  CLI sweeper, which correctly refuses project-owned cards ("its run
  promotes it, not the sweeper"). The dispatcher (confirmed healthy)
  claimed and spawned a worker within one tick.
- **Fixed** in [#389](https://github.com/leolau/ai-prentice-4-all/pull/389):
  `promote_run_cards()` now splits candidates into dependency-satisfied
  vs. not (ready ones promoted first), tie-broken by playbook position
  instead of the incidental DB row order — exactly the suggested fix
  below, implemented as described.

## Gap B/C/D implementation notes (deviations from the plan's §3.1–§3.3 sketch)

- **Gap D** landed inside the existing `promote_run_cards()` in
  `hermes_cli/projects_run.py` rather than a new function — it's the same
  loop, just re-ordering its candidate list before promoting. One extra
  `SELECT ... WHERE id IN (...)` per call to snapshot every run card's
  current status once (used both for the `triage` filter and for
  `_deps_satisfied()`), replacing the old per-row `SELECT`.
- **Gap B**'s `reconcile_all_open_runs()` and `_reconcile_one_run()` landed
  in `hermes_cli/projects_reconcile.py` (the module Gap A already created),
  not a new module — matches the plan's naming closely (`reconcile_run`
  from the sketch became the inline `_reconcile_one_run` per-run helper;
  the public entry point is `reconcile_all_open_runs`).
- The shared liveness read is `projects_reconcile.run_last_activity_at(pconn,
  project, run)` (plan called it `run_last_activity_at` too — landed
  as-designed). It opens its own board connection
  (`kanban_db.connect_closing(board=project.board_slug or None)`) rather
  than requiring the caller to already have one open, so both the sweep
  and `doctor`/`health` can call it with only a projects-db connection in
  hand.
- **Idempotency against two gateways**: before closing a stale run,
  `_reconcile_one_run()` re-reads the run fresh from the DB and checks it's
  still `running` — added because the shared root Projects store has no
  per-profile ownership, so two profiles' gateways could both be sweeping
  the same project. Not covered by a real two-process test (out of scope
  for a unit test), but covered by
  `test_reconcile_is_idempotent_against_an_already_closed_run` (simulates
  the race by closing the run between `start_run()` and the sweep call).
- **Gateway wiring**: a new sibling watcher method
  `_projects_reconcile_watcher()` in `gateway/kanban_watchers.py`, started
  via `asyncio.create_task()` in `gateway/run.py::start()` right next to
  the existing `_kanban_dispatcher_watcher()` call — additive, per the
  plan's "extend the existing watcher loop, don't add a new service"
  constraint. Deliberately has **no singleton lock** (unlike the kanban
  dispatcher's `.dispatcher.lock`) — every write it makes goes through
  primitives (`specify_triage_task`, `close_project_run`) that are already
  transaction-safe against concurrent callers; a lock would add complexity
  for a failure mode (duplicate no-op / doubled notification) that's
  already harmless.
- **Gap C** required threading a new optional `pconn` parameter through
  `derive_health()` (not in the original plan sketch, which only mentioned
  extending `doctor_findings`) — `derive_health()` didn't take a connection
  at all before this, and the run-staleness check needs one. Both call
  sites in `hermes_cli/projects_api.py` (`_full_health` and
  `project_doctor_route`) were updated to pass it; any other caller that
  omits `pconn` keeps the old behavior unchanged (tested).
- Config: `projects.run_stall_seconds` (default `7200`) and
  `projects.reconcile_interval_seconds` (default `300`) landed exactly as
  specced in `projects_run.projects_runtime_config()`.
- **Verification**: the first full-suite run (raw `pytest file1 file2 ...`
  across ~300 files in one process) produced several false failures,
  including one of this PR's own new tests — traced to cross-file global-
  state/async-mock pollution from combining that many files in a single
  pytest process, NOT anything this change broke. Confirmed by re-running
  through the repo's actual canonical runner
  (`scripts/run_tests.sh`, which isolates each file in its own subprocess
  per `AGENTS.md`/the script's own docstring) — 986 passed, 2 failed, both
  pre-existing and unrelated (verified identical via `git stash` against
  unmodified `develop`). Lesson for next time: use `scripts/run_tests.sh`
  from the start, not a bare multi-file `pytest` invocation.
- **Not deployed to production** — built and tested locally only, per an
  explicit request while investigating/fixing this to not touch the
  Hetzner box again this round. The already-orphaned run
  (`run_91b11d845e73`) was already manually recovered before this code
  existed (see row 6); B/C/D landing in production will make the *next*
  incident like it self-heal instead of needing another manual
  investigation.

## How to update this file

When a PR for one of the rows above merges to `develop`:
1. Flip its Status to `Done` and fill in the PR link.
2. If the change altered the design (e.g. a different config default, a
   different call site than planned), add a short note in that row rather
   than editing the plan doc's prose — the plan doc stays the original
   design record; this file is the diff against it.
3. If a row is abandoned or superseded, mark it `Dropped` with a one-line
   reason, don't delete it.
