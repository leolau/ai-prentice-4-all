# Implementation status: Project run resilience (refill + restart recovery + doctor)

Companion to `plans/2026-09-13-project-run-resilience-plan.md`. Update this
file as work lands — one row per gap, per the plan's §7 breakdown.

| # | Gap | Description | Status | PR / commit | Notes |
|---|-----|-------------|--------|--------------|-------|
| 1 | A | `reconcile_run()` refill hook (kanban_db → projects_reconcile → promote_run_cards on card settle) | **Not started** | — | Highest-value, no config needed |
| 2 | B | `reconcile_all_open_runs()` + `run_stall_seconds`/`reconcile_interval_seconds` config + gateway watcher wiring + auto-fail stale runs | **Not started** | — | Depends on #1's `reconcile_run()` |
| 3 | C | `doctor_findings`/`derive_health` `run_stalled` code (all cadences) | **Not started** | — | Read-only, can ship independently |
| 4 | — | FG-32 §20.2 pointer to this defect + plan doc | **Not started** | — | Do after #1–#3 merge |
| 5 | — | Tests (unit: refill, reconcile, doctor; gateway watcher interval; E2E full-run-without-manual-continue) | **Not started** | — | Written alongside each of #1–#3, per plan §5 |
| 6 | — | Production cleanup: orphaned run `run_91b11d845e73` on `ai-professsional-builder-course-ppt` | **Not started** | — | Expected to self-resolve once #2 deploys; manual triage only if it doesn't |

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

## How to update this file

When a PR for one of the rows above merges to `develop`:
1. Flip its Status to `Done` and fill in the PR link.
2. If the change altered the design (e.g. a different config default, a
   different call site than planned), add a short note in that row rather
   than editing the plan doc's prose — the plan doc stays the original
   design record; this file is the diff against it.
3. If a row is abandoned or superseded, mark it `Dropped` with a one-line
   reason, don't delete it.
