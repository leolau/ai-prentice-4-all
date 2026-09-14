# Session hand-off — Projects run resilience: refill, restart recovery, doctor, promotion order (2026-09-12/13)

Written at a natural close: the triggering incident is resolved, all four
fixes are merged **and deployed to production**, and the specific run that
started this investigation is confirmed self-propelling. Nothing here is
blocked or half-done — this is a closeout note plus the follow-ups worth
someone's attention next.

The design/investigation record lives in
[`plans/2026-09-13-project-run-resilience-plan.md`](../../plans/2026-09-13-project-run-resilience-plan.md)
(the design) and
[`plans/2026-09-13-project-run-resilience-status.md`](../../plans/2026-09-13-project-run-resilience-status.md)
(the per-gap status table — keep that one updated going forward, it's more
granular than this note). Read the plan doc first for the *why*; this is the
*where we got to*, written for someone who wasn't in the room.

## What happened, in one paragraph

A production project run (`ai-professsional-builder-course-ppt`, run #1) sat
`status: running` for 13+ hours with zero cards ever started, and
`hermes projects doctor` reported nothing wrong. Root-causing it surfaced
four independent defects in the Projects feature (FG-32) — not one bug, four —
and fixing them required two production deploys, one accidental
misconfiguration I caused and fixed mid-session (documented below so it
isn't repeated), and one real discovery of two *other*, unrelated dead runs
that the new code found and fixed for free on its first sweep.

## 1. What is done and live in production (all four gaps, verified)

| Gap | What | PR | Where |
|---|---|---|---|
| A | A run's promoted cards refill automatically when a card completes (previously only fired at `start_run()`/`continue_run()`) | [#388](https://github.com/leolau/ai-prentice-4-all/pull/388) | `hermes_cli/projects_run.py::refill_run_cards`, `hermes_cli/projects_reconcile.py::on_card_settled`, hooked into `hermes_cli/kanban_db.py`'s 3 card-settle call sites |
| B | A periodic sweep refills every open run's capacity independent of any single completion event, and auto-fails a run that's gone stale with nothing left to promote/in flight (raises an FG-10 approval) | [#389](https://github.com/leolau/ai-prentice-4-all/pull/389) | `hermes_cli/projects_reconcile.py::reconcile_all_open_runs`, `gateway/kanban_watchers.py::_projects_reconcile_watcher`, wired in `gateway/run.py::start()` |
| C | `hermes projects doctor` / list-page health now flag a stuck `running` run for **every** cadence (previously only repeatable's schedule-silence and one_off's due-date were checked) | [#389](https://github.com/leolau/ai-prentice-4-all/pull/389) | `hermes_cli/projects_schedule.py::doctor_findings`/`derive_health`, new `run_stalled` code |
| D | `promote_run_cards()` picked which `triage` card to promote by **task-ID sort order**, not dependency readiness — this is what actually deadlocked the incident's run, twice, at two different depths of the playbook | [#389](https://github.com/leolau/ai-prentice-4-all/pull/389) | `hermes_cli/projects_run.py::promote_run_cards` |

Config added (`config.yaml` `projects:` section, both fail-open with
defaults): `run_stall_seconds` (7200 = 2h) and `reconcile_interval_seconds`
(300 = 5min).

**Production state at the time these four gaps were deployed**: box was on
`7bf88d4e7` (both PRs deployed via the reviewed
`/opt/data/deploy-hermes.sh develop`, not a raw restart). Several more
rounds of unrelated work have shipped and deployed since (chat/projects
perf, blocked-card UX, a kanban auto-retry timer, Folder Bridge) — see
[`plans/2026-09-14-session-handoff.md`](../../plans/2026-09-14-session-handoff.md)
for that history; the box is on a newer SHA by the time you read this.
None of it touches the Projects run-resilience code this note describes.

Verified live (at deploy time):

- The kanban dispatcher and the new `_projects_reconcile_watcher` both start
  cleanly and log their startup lines
  (`kanban dispatcher: holding singleton dispatcher lock`, and
  `projects reconcile: {...}` on each sweep).
- Within 40 seconds of the deploy's restart, the sweep **correctly** refilled
  `ai-professsional-builder-course-ppt` run #1 (promoted the actual next
  step this time, not a blocked sibling — Gap D's fix working end to end in
  prod) and **auto-failed two genuinely dead runs** on a different project
  (`ai-i-train-the-trainer-primary`, runs #1 and #2 — 311h and 307h stale,
  zero deliveries, `session_id: null`). Not a false positive: both had
  clearly been abandoned, not slow.
- The incident's run then progressed on its own with zero further manual
  intervention through 5 of its 7 cards (Canva presentation setup → style
  prompt → 100 concept prompts → 29 lab prompts → the review checkpoint,
  which got the owner's chat approval and completed), and correctly **held**
  at the checkpoint's successor (`execute-canva-prompts`) waiting for an
  explicit "Continue" — see §3 for why that's by design, not a fifth bug.

## 2. A mistake made and fixed mid-session — read before touching the gateway unit

While reinstalling the gateway's systemd unit to fix an unrelated stale
`TimeoutStopSec` (found in the same investigation), a **second**
`hermes gateway install --force` / `hermes gateway restart` call was run in a
shell where `HERMES_HOME` had NOT been exported. `hermes gateway install`'s
auto-refresh logic silently regenerated the unit using the *calling shell's*
`HERMES_HOME` resolution (root's default `~/.hermes`, remapped to
`/opt/data/hermes-user/.hermes` for the `hermes` user) instead of the box's
real `/opt/data/hermes-home-staging`. The gateway ran against a fresh, wrong
home for ~9 minutes before this was caught (via the log file's destination
path, `/proc/<pid>/environ`, not the systemd unit file, which looked fine at
a glance).

**Lesson, spelled out for whoever touches this next:** `hermes gateway
install --force` (and the auto-refresh `restart` triggers) recompute
`HERMES_HOME` from `get_hermes_home()` — i.e. from **whatever environment the
CLI invocation itself runs in**, not from the currently-installed unit's
`Environment=` line (unless that line happens to already exist and gets
read back — it didn't, because the original unit predates this tool's
current output shape). Always `export HERMES_HOME=/opt/data/hermes-home-staging`
in the *same* shell invocation as any `hermes gateway install`/`restart`
call on this box. The stray files this created under
`/opt/data/hermes-user/.hermes` were identified by `mtime` and cleaned up
(the directory itself is a separate, dormant "default profile" home that
predates this session — left alone, not touched further).

Also fixed while there: the regenerated unit's `ExecStart` bypasses the old
`/opt/data/start-gateway.sh` wrapper, which was the only thing sourcing
`/opt/data/hermes-staging.env` (`DATABASE_URL` — deliberately kept outside
`$HERMES_HOME` so the CLI's own `.env` auto-load never picks it up, see
`docs/deployment/PRODUCTION.md`). Re-added via a drop-in,
`/etc/systemd/system/hermes-gateway.service.d/20-database-url.conf`
(`EnvironmentFile=/opt/data/hermes-staging.env`), which survives future
`install --force` runs (drop-ins aren't touched by the regenerator, only the
main unit file is).

## 3. Not a bug, but worth knowing: checkpoint chat-approval ≠ Projects' own continue gate

The incident's run reached its playbook checkpoint (`review-drafted-prompts`,
`checkpoint: true`). The worker on that card got the owner's approval through
its own chat conversation and completed normally — but `execute-canva-prompts`
(the checkpoint's successor) stayed **held** regardless, because
`held_step_keys()` in `promote_run_cards()` doesn't know anything about what
happened inside the completed card; it only knows "this step follows a
checkpoint step" and requires an explicit `continue_run()` call (the
agent-home "Continue" button, or `POST /api/projects/{slug}/runs/{run_no}/continue`)
to release it. This is very likely the *intended* design (§4/§8.2: "a human
approves every crossing" is meant to be a real crossing, not implied by
something a worker decided in an unrelated conversation) — but it does mean
a project can look "approved and done" in chat while the run itself is still
sitting there waiting for a UI click nobody's told to make. Worth a
maintainer's read of FG-32 §4.3/§8.2 to confirm this is deliberate rather
than an oversight; if it's deliberate, a small win would be surfacing "this
run needs your Continue" more visibly than a run-page button (e.g. a
proactive nudge), since `raise_approval()` currently only fires once, at
`start_run()` time, announcing the checkpoint *exists* — not again when the
checkpoint card actually completes and the successor is sitting ready to be
released.

## 4. Not started — the exact next steps

1. **FG-32 §20.2 pointer.** The plan doc said to add one once B/C/D shipped
   — do it now that they're deployed and verified:
   [`docs/design/master-plan/feature-groups/FG-32-projects-durable-record.md`](./master-plan/feature-groups/FG-32-projects-durable-record.md)
   §20.2 should get a short entry pointing at
   `plans/2026-09-13-project-run-resilience-plan.md` (the fifth review-style
   entry in that section, following the existing "review of ..." pattern).
2. **`ai-i-train-the-trainer-primary`** now has two runs correctly marked
   `failed`/`stalled` instead of silently stuck `running`. Nobody has looked
   at *why* those runs actually died (over a week ago) — that's a separate
   investigation the owner may want, now that it's visible instead of
   invisible.
3. **Test-runner trap, worth a project-wide note somewhere more visible than
   this file**: a raw multi-file `pytest file1.py file2.py ...` invocation
   across ~300 files produces cross-file global-state / async-mock-leak false
   failures (one of which was this session's *own* new test, which passed
   cleanly alone and failed only when combined with unrelated files). The
   repo's `scripts/run_tests.sh` isolates each file in its own subprocess
   specifically to avoid this — always use it, not a bare `pytest` sweep,
   when the signal matters (verified by reproducing and then re-running the
   isolated way; documented in the status doc's "Gap B/C/D implementation
   notes" section).
4. **The `promote_run_cards()` DB-order finding** (Gap D) was fixed for the
   "which card promotes first under a narrow cap" case, but nobody has
   audited whether the same "no natural order" property of
   `projects_db.get_run_cards()` matters anywhere else it's called (it isn't
   ordered by playbook position at the SQL level — the fix sorts in Python
   after fetching). Low risk, not urgent, just noted.

## 4b. Where the incident's specific run actually is now (2026-09-14)

For continuity: the exact run this note is about
(`ai-professsional-builder-course-ppt` run #1) kept moving on its own past
the point described in §1 above, then hit a **real, unrelated** blocker —
`execute-canva-prompts` ran into Canva's daily API generation quota after
112/131 slides (`status: blocked`, confirmed live on the box as of this
writing). That is not a Projects-engine bug; it's what motivated the
blocked-card-UX and kanban-auto-retry-timer work in
[`plans/2026-09-14-session-handoff.md`](../../plans/2026-09-14-session-handoff.md),
which is now live but doesn't retroactively apply to a card blocked before
it shipped. That note's own "what's still open" section already covers the
exact next step (check if the quota's reset, ask before touching it) —
don't duplicate that decision here, read it there.

## 5. Multiple agents share this checkout

This worktree was found ~22 commits behind `origin/develop` with a pile of
working-tree content that turned out to be stale, untracked leftovers from
an earlier local edit — every one of them, byte-for-byte, matched what
`origin/develop` already had tracked (confirmed with `diff` before touching
anything). Resolved by removing the untracked duplicates and fast-forwarding
(`git pull --ff-only`); nothing was lost, nothing of another session's
in-progress work was discarded. If you're picking this up and see the same
pattern (untracked files blocking a fast-forward), diff them against
`origin/develop` first — if they're identical, it's safe to delete and pull;
if they differ, they're someone's real uncommitted work and need asking
about, not discarding.
