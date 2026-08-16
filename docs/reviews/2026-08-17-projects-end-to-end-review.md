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
| H1 | checkpoint/budget approvals never raised (`from agent import human_comms` — module does not exist; the shipped seam is `hermes_cli.human_comms.NotificationStore.create`) | **OPEN** | `projects_run.py:528-546` |
| H2 | budget unenforceable — `sum_cost_for_trace` does not exist and `trace_id` is a synthetic string never bound to a trace | **OPEN** | `projects_run.py:548-566`, `592-593`, `641` |
| H3 | `_enabled_toolsets_for_profile(profile)` ignores its argument and reads the *calling* process's config, so a project can be granted a toolset the host profile disables (invariant 14) | **OPEN** | `projects_run.py:827-841` |
| H4 | inline run spawns without `profile_home`, so a manual run executes in the server's profile while the run row records the host profile | **OPEN** | `projects_run.py:869-894` |
| M1 | a repeatable project that has *never* run is never `stalled` (`if last_start and …`) | **OPEN** | `projects_schedule.py:423-428` |
| M2 | health/read filtering happens **after** the page slice, and `next_cursor` is taken from the last *emitted* row | **OPEN — and worse than reported: it loses rows, not just repeats them.** Rows between the last emitted row and the end of the slice are skipped permanently, and an all-filtered page returns `next_cursor=None`, ending pagination while matches remain | `projects_api.py:493-528` |
| M3 | an instance owner/admin who is not a project member has `role is None`, so contact addresses are dropped from them too | **OPEN** | `projects_api.py:802` |
| L1 | `toolsets`/`skills` stored as CSV strings | **OPEN** | `projects_run.py:646-652` |
| L2 | profile-imported legacy projects land with NULL `goal`, no outputs and no host profile — the mandatory-field invariant does not hold for them | **OPEN** | `projects_db.py:2496-2517` |
| F1a | accept-output returns an ack envelope; `OutputsPanel` merges it as a row, so the row stays `delivered`, the button stays, and `offers_closure` is never surfaced | **OPEN** | `projects_api.py:1021-1025` vs `OutputsPanel.tsx:38-59` |
| F1b | continue-run returns `{run, promoted, budget_gate}`; `RunView` reads `data.status`, which never exists at the envelope level, so continuing appears to do nothing and the `budget_gate` holding the run is never shown | **OPEN** | `projects_run.py:786` vs `RunView.tsx:84-86` |
| F1c | add-directive returns `{id, applies_from}`; `GuidancePanel` prepends it as a `ProjectDirective`, so the new directive renders with no body, author or date until reload | **OPEN** | `projects_api.py:1691` vs `GuidancePanel.tsx` |
| F2 | the Attention chip filters `health=attention` by equality, so a `stalled` project — the one that outranks attention — is excluded from the very view meant to surface it | **OPEN** | `filters.ts:57,77-78` + `projects_api.py:511-512` vs `projects_schedule.py:405` |
| F3 | `@router.get("/")` / `@router.post("/")` make every list and create pay a 307 (the todos router uses `""`) | **OPEN** | `projects_api.py:532,561` |
| F4 | `RunView` never revalidates after a write — no `router.refresh()` on any path | **OPEN** | `RunView.tsx:62-115` |
| F5 | a `waiting` run is hidden once it falls out of the five-run brief | **OPEN** | `projects_api.py:376-399` |
| F6/F7 | upstream error detail/path leakage; 404s rendered as raw load errors | **OPEN** | BFF bridge + detail page |
| F8 | step 8b (`from_todo`) not on `develop` | **FIXED** — landed in `ffb139319` (#279/#280); see E2 below | `projects_api.py:1400-1519` |

## New findings

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
machine operator's terminal is trusted. The defect is narrower and worth fixing
on its own terms: the human-only acts need one shared gate, applied at all
three routes, that the agent's own surface cannot satisfy. Options that keep
the operator surface working: let the CLI patch only the principal seams and
require an explicit `--as-human` confirmation that writes the acting subject,
or move the human check to a per-act `accepted_by`/`scored_by` provenance the
CLI must supply from a real session. Whichever is chosen, `SKILL.md` should
stop describing an unenforced rule as a rule.

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

Second half: when the stage move fails, the card is deleted
(`projects_api.py:1505-1516`) but the `project_links` row written inside
`_create_sync` is **not** — the project keeps a `kind='todo'` pointer to a
promotion that did not happen, and because the insert is `INSERT OR IGNORE`,
re-promoting the same to-do later leaves the stale `label`/`added_by` in place.
Roll the link back with the card.

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
drop the endpoint until the consumer lands. The `summarise` half of step 11 is
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

### E5 — the derived project score silently disappears behind 25 unscored runs (low)

`_derived_score` reads at most the last 25 runs and *then* takes the first five
scored ones (`projects_api.py:363-373`). A standing project with 25 unscored
recent runs reports no score at all, though §8.1 says the project score is the
mean of the last five *scores*. Either select `WHERE score_user IS NOT NULL
ORDER BY run_no DESC LIMIT 5` in the store, or say in the payload that the
window is the last 25 runs.

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
