---
title: "design: Projects — the durable record of a piece of work that can be reviewed, repeated and learnt from"
status: draft — spec for review, then implementation
date: 2026-08-13
type: feature design (implementation-ready, standalone)
target_repo: ai-prentice-4-all
audience: an implementing agent with no other context than this file and the tree
origin: |
  Leo, 2026-08-13: "a project stores all the related information about a project
  which can be long lasting, repeatable or one-time only. It can be fully
  automatic or it can require user to provide instructions, feedback and
  guidance over time. Everything is tracked properly in Project so that it can
  be reviewed, repeated and learnt from over time."
consolidates:
  - docs/plans/2026-08-12-001-projects-page-plan.md (ed.1 — the substrate review, the record, the page)
  - docs/plans/2026-08-13-001-todos-and-projects-design-revision.md (ed.2 — Projects' six open questions answered; the to-do → card seam)
extends_with:
  - cadence (one-off / repeatable / standing) — new
  - autonomy (manual / supervised / autonomous) and its caps — new
  - guidance (durable directives + feedback, and when they take effect) — new
  - runs, retros and the learning loop — new
depends_on:
  - hermes_cli/kanban_db.py            # the execution substrate (statuses, links, runs, events, attachments)
  - hermes_cli/projects_db.py          # the Project record; already carries board_slug, project_profiles, project_links
  - hermes_cli/todo_store.py           # "what needs me next" — a project asks through it
  - hermes_cli/human_comms.py          # FG-10 approvals + proactive asks ('approval' | 'proactive_ask')
  - cron/jobs.py, cron/scheduler.py    # the shipped, profile-scoped schedule engine a repeatable project uses
  - agent/background_review.py         # the shipped skill-distillation loop the learning path ends in
  - hermes_cli/interactions.py         # C8 trace ledger; kind='cost' is where a run's cost comes from
  - docs/design/master-plan/feature-groups/FG-27-profile-scoped-datastore-isolation.md
  - docs/design/master-plan/feature-groups/FG-28-multi-profile-administration.md
  - docs/design/master-plan/feature-groups/FG-29-goal-tree-and-insight-promotion.md
---

# Projects

## 0. How to read this document

This is the **single file an implementer needs** for Projects. It restates the
parts of ed.1/ed.2 an implementer cannot work without, and adds the four
dimensions those documents do not cover (cadence, autonomy, guidance, runs and
learning). Where it disagrees with ed.1/ed.2, **this document wins** — the
diffs are listed in §17.

Ground truth as of `develop` @ 2026-08-13:

| piece | state |
|---|---|
| `projects` / `project_folders` / `project_meta` / `discovered_repos` | **shipped**, per-profile at `$HERMES_HOME/projects.db` |
| `project_profiles` / `project_links` (+ `add_project_link`, `get_project_profiles`) | **shipped** in `projects_db.py` (added for ed.1), unused by any surface |
| `tasks.project_id` on the kanban board | **shipped**, used for worktree/branch anchoring |
| `project_members`, the `projects` HTTP router, the `agent-home` pages, `hermes projects` | **not built** |
| everything in §3–§8 of this document (cadence, autonomy, guidance, runs, playbook, retro) | **not built, not previously designed** |

Two repo rules govern every decision below, and an implementer who breaks
either has broken the feature (`AGENTS.md`):

1. **Per-conversation prompt caching is sacred.** Nothing here injects text into
   a live conversation. Guidance is *compiled into a run's seed prompt at spawn*
   — which is why §5's "takes effect next run" is a design property, not a
   limitation.
2. **The core is a narrow waist.** No new model tool. The agent reaches projects
   through `hermes projects …` + a skill (footprint rung 2), exactly as
   `incomings` and `goal` do.

---

## 1. What a Project is

To-dos answer *what needs me next*. A **Project** answers **what are we trying
to finish, how does it get done, who and what is involved, and what did we learn
last time?**

A Project is four things at once, and the design keeps them separate on purpose:

```
  ┌─ the commitment ──────────────────────────────────────────────────────────┐
  │ purpose (goal links) · cadence (one-off | repeatable | standing)          │
  │ definition of done · autonomy · caps and budget                          │
  └──────────────────────────────────────────────────────────────────────────┘
  ┌─ the method ─────────────────────────────────────────────────────────────┐
  │ the playbook (ordered card templates, versioned) + the guidance the user  │
  │ has given over time (durable directives, run feedback)                    │
  └──────────────────────────────────────────────────────────────────────────┘
  ┌─ the work ───────────────────────────────────────────────────────────────┐
  │ kanban cards (already shipped: statuses, parent links, runs, comments,    │
  │ attachments, events, circuit breaker) grouped by tasks.project_id         │
  └──────────────────────────────────────────────────────────────────────────┘
  ┌─ the record ─────────────────────────────────────────────────────────────┐
  │ runs (one row per occurrence) · retros · linked files, arrivals, to-dos,  │
  │ memory docs, conversations, URLs · members and profiles · C8 traces       │
  └──────────────────────────────────────────────────────────────────────────┘
```

**The method is the part that makes a project repeatable, and the record is the
part that makes it learnable.** Everything under "the work" already exists and
is already tested; this design adds no execution engine, no second board, no
second scheduler and no second status vocabulary.

### 1.1 The key information a Project holds

This is the field-level answer to "what does a project store". Every row is
either shipped, or specified in this document at the section named.

| group | information | where it lives |
|---|---|---|
| identity | `id`, `slug`, `name`, `description`, `icon`, `color` | `projects` (shipped) |
| purpose | linked goals (`project_links.kind='goal'`), `definition_of_done` | shipped table / §2.2 |
| commitment | `cadence`, `schedule`, `review_every`, `due_at`, `status` | §2.2, §3 |
| autonomy | `autonomy`, `max_in_progress`, `budget_usd_per_run`, `require_approval` | §2.2, §4 |
| ownership | `owner_user_id`, `visibility`, members + roles, profiles + roles (incl. the **host** profile) | §2.2, `project_members` (§2.2), `project_profiles` (shipped) |
| method | the playbook (versioned card templates), the guidance log | `project_playbook` / `project_directives` (§2.2, §5, §7) |
| place | `primary_path`, folders, bound board (`board_slug`) | `projects` / `project_folders` (shipped) |
| work | cards, their runs, comments, attachments, events, blocked reasons | `kanban_db` (shipped), joined by `tasks.project_id` |
| context | linked files, arrivals, to-dos, memory documents, conversations, URLs | `project_links` (shipped) |
| history | runs, per-run outcome + retro, C8 traces, cost | `project_runs` (§2.2, §6, §8) |
| state | `status`, rolling `summary` + `summary_at`, `next_run_at`, health, `last_reviewed_at` | §2.2, §9 |

Derived numbers (progress, card counts, cost, "3 running · 1 blocked") are
**never stored** — they are computed on read from the board, the goal tree and
the C8 ledger. A stored count is how two surfaces start disagreeing.

### 1.2 What a Project is not

- **Not a to-do.** A to-do is one decision for a human. A project is a piece of
  work that spans sittings, people or profiles. The seam between them is §10.
- **Not a second board.** A project's work *is* kanban cards.
- **Not a second scheduler.** A repeatable project's schedule *is* a `hermes cron`
  job in its host profile (§3.2).
- **Not a chat log.** A conversation is linked, not owned; guidance the user
  wants to persist is a directive (§5), not a message in scrollback.

---

## 2. The record

### 2.1 Where it lives: promote `projects.db` to the shared root

A project worked on *with other profiles* cannot live in one profile's
`$HERMES_HOME`. Options considered and rejected in ed.1, restated because an
implementer will re-ask:

| option | verdict |
|---|---|
| Postgres `projects` table in the app schema | **Rejected.** FG-27 makes the app schema profile-derived, so the project would belong to exactly one profile and a second profile would create an invisible copy of the same project. |
| A new root-anchored store beside `projects.db` | **Rejected.** Two project registries. |
| **Move `projects.db` to the shared root** (`kanban_home()/projects.db`), keep the schema, add tables | **Chosen.** It already carries `board_slug`, `primary_path`, folders, slug, and the board already carries `tasks.project_id`. `kanban_home()` is the same resolver the board uses, so the two can never disagree about which root they are on. |

Mechanics:

- `projects_db.projects_db_path()` resolves to `kanban_db.kanban_home() / "projects.db"`,
  with `HERMES_PROJECTS_DB` as the explicit override (mirroring `HERMES_KANBAN_HOME`)
  that tests and odd deployments use.
- **Migration on first open of the root DB:** import every profile's
  `$HERMES_HOME/projects.db` rows; slug collisions get a `-2` suffix; each
  imported row records `imported_from_profile`. The per-profile file is left in
  place, untouched and unread — so the migration is reversible by pointing
  `HERMES_PROJECTS_DB` back, and nothing deletes a user's data to satisfy a
  refactor. The import is idempotent (keyed on `imported_from_profile` + old id).
- **The desktop and `hermes project` are repointed in the same PR** (ed.2 Q5).
  Two project lists on one box reads to a user as data loss.

### 2.2 New tables and columns (all additive, `projects_db` style)

```sql
-- ── people ────────────────────────────────────────────────────────────────
-- A member is a *person* (GoTrue subject, box-wide since the shared-GoTrue
-- decision). A participant is a *profile* (an instrument the work runs on).
-- Both lists are needed and they are not the same list.
CREATE TABLE IF NOT EXISTS project_members (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',   -- lead | member | viewer
    added_by    TEXT,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

-- ── the method: a versioned playbook ─────────────────────────────────────
-- One row per revision. `steps` is a JSON array of card templates (§7).
-- A new revision is created inactive and activated by a human.
CREATE TABLE IF NOT EXISTS project_playbook (
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rev          INTEGER NOT NULL,
    body         TEXT NOT NULL DEFAULT '',   -- prose: what this project does, in the user's words
    steps        TEXT NOT NULL DEFAULT '[]', -- JSON: ordered card templates
    active       INTEGER NOT NULL DEFAULT 0,
    created_by   TEXT,
    created_at   INTEGER NOT NULL,
    activated_at INTEGER,
    note         TEXT,                        -- why this revision exists (often a retro id)
    PRIMARY KEY (project_id, rev)
);

-- ── the method: guidance the user gives over time ────────────────────────
-- A directive is a standing instruction; feedback is a judgement about a run
-- or a card. Both are durable and both are compiled into future run prompts
-- (§5). Neither is ever injected into a live conversation.
CREATE TABLE IF NOT EXISTS project_directives (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,              -- directive | feedback
    body          TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'project',  -- project | run | card
    target_ref    TEXT,                       -- run_no or task_id when scope != project
    rating        TEXT,                       -- feedback only: good | bad (null for directive)
    author_user_id TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    retired_at    INTEGER,
    superseded_by TEXT                        -- another directive id
);
CREATE INDEX IF NOT EXISTS idx_project_directives_active
    ON project_directives(project_id, active, created_at DESC);

-- ── the record: one row per occurrence ───────────────────────────────────
CREATE TABLE IF NOT EXISTS project_runs (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_no        INTEGER NOT NULL,
    trigger       TEXT NOT NULL,     -- schedule | manual | event | review
    triggered_by  TEXT,              -- user id, or 'cron:<job_id>'
    profile       TEXT NOT NULL,     -- the host profile the run executed under
    playbook_rev  INTEGER,           -- NULL for a run with no playbook (ad-hoc work)
    status        TEXT NOT NULL,     -- running | waiting | blocked | done | failed | cancelled
    started_at    INTEGER NOT NULL,
    ended_at      INTEGER,
    session_id    TEXT,              -- SessionDB id of the seeded session, when there is one
    trace_id      TEXT,              -- C8 trace; cost and steps are read from it
    outcome       TEXT,              -- one line, machine-set on close
    summary       TEXT,              -- the agent's account of the run
    retro         TEXT,              -- the retrospective (§8); NULL until written
    retro_at      INTEGER,
    error         TEXT,
    UNIQUE (project_id, run_no)
);
CREATE INDEX IF NOT EXISTS idx_project_runs_recent
    ON project_runs(project_id, run_no DESC);

-- Which cards belong to which run. A card may exist without a run (someone
-- created it by hand); a run may create many cards.
CREATE TABLE IF NOT EXISTS project_run_cards (
    run_id    TEXT NOT NULL REFERENCES project_runs(id) ON DELETE CASCADE,
    task_id   TEXT NOT NULL,
    step_key  TEXT,                  -- the playbook step it came from, when any
    PRIMARY KEY (run_id, task_id)
);

-- ── additive columns on `projects` ───────────────────────────────────────
ALTER TABLE projects ADD COLUMN visibility            TEXT NOT NULL DEFAULT 'shared';
ALTER TABLE projects ADD COLUMN owner_user_id         TEXT;
ALTER TABLE projects ADD COLUMN status                TEXT NOT NULL DEFAULT 'active';
                                  -- planning | active | paused | done | archived
ALTER TABLE projects ADD COLUMN cadence               TEXT NOT NULL DEFAULT 'one_off';
                                  -- one_off | repeatable | standing            (§3)
ALTER TABLE projects ADD COLUMN schedule              TEXT;    -- cron/jobs syntax; repeatable only
ALTER TABLE projects ADD COLUMN review_every          TEXT;    -- e.g. '7d'; standing only
ALTER TABLE projects ADD COLUMN autonomy              TEXT NOT NULL DEFAULT 'supervised';
                                  -- manual | supervised | autonomous           (§4)
ALTER TABLE projects ADD COLUMN max_in_progress       INTEGER NOT NULL DEFAULT 1;
ALTER TABLE projects ADD COLUMN budget_usd_per_run    REAL;    -- NULL = no cap
ALTER TABLE projects ADD COLUMN definition_of_done    TEXT;
ALTER TABLE projects ADD COLUMN due_at                INTEGER;
ALTER TABLE projects ADD COLUMN host_profile          TEXT;    -- where runs execute and the cron job lives
ALTER TABLE projects ADD COLUMN cron_job_id           TEXT;    -- the shipped scheduler's job id (§3.2)
ALTER TABLE projects ADD COLUMN summary               TEXT;    -- rolling "where this stands"
ALTER TABLE projects ADD COLUMN summary_at            INTEGER;
ALTER TABLE projects ADD COLUMN last_reviewed_at      INTEGER;
ALTER TABLE projects ADD COLUMN next_run_at           INTEGER; -- cache of the cron job's next fire, display only
ALTER TABLE projects ADD COLUMN imported_from_profile TEXT;
```

Column additions go through `hermes_cli/sqlite_util.add_column_if_missing`, as
`projects_db._migrate_add_optional_columns` already does, so opening an old DB
is always safe.

### 2.3 Two small, additive changes to `kanban_db`

- `list_tasks(..., project_id=…)` — one more `AND project_id = ?` in the existing
  builder (`kanban_db.py:2793`). Without it every project read filters the whole
  board in Python.
- `CREATE INDEX idx_tasks_project ON tasks(project_id, status)` through the
  existing additive-index path.

**No new columns on `tasks`.** In particular: no `run_id` column — the mapping
lives in `project_run_cards`, so the shared board learns nothing about
Projects. Direction of dependency: **Projects knows about the board, to-dos and
goals; none of them learn about Projects.**

`workflow_template_id` / `current_step_key` exist on `tasks` but are documented
forward-compat columns that the dispatcher does not consult. **Do not build the
playbook on them** — stamping them would imply routing that does not exist. §7
uses parent links instead, which the dispatcher *does* honour.

---

## 3. Cadence — one-off, repeatable, standing

`cadence` is a **commitment about when the project ends**, and it is the field
that decides what the rest of the system does with it. (This mirrors how FG-29
made goal *lifetime* load-bearing rather than decorative.)

| cadence | ends when | must have | drives |
|---|---|---|---|
| `one_off` | its `definition_of_done` is met; a human sets `status='done'` | `definition_of_done` | cards; at most one run |
| `repeatable` | never — each occurrence ends | `schedule` + an **active playbook** | one cron job → one run per fire |
| `standing` | never; it is **retired**, not finished | `review_every` | a review run/to-do each period; continuous linking |

### 3.1 Rules that fall out of it

- A `repeatable` project **must** have an active playbook before a schedule can
  be set. A schedule with no method is a timer that produces nothing; refuse it
  at the API with a 409 naming the missing playbook.
- A `one_off` project **may** have a playbook (that is how a one-off becomes
  repeatable later: "Make this repeatable" copies rev 1 and asks for a schedule).
- A `standing` project shows *"last reviewed <n> days ago"* rather than a
  completion percentage, and its review period elapsing raises a to-do (§10),
  not a card — a review is a human act.
- Changing cadence is allowed and is a single `PATCH`, but
  `repeatable → one_off` **pauses and detaches the cron job** (never deletes it
  silently) and records a directive-log entry naming who changed it.

### 3.2 A repeatable project's schedule is a `hermes cron` job

We do not write a scheduler. `cron/jobs.py` + `cron/scheduler.py` already ship
schedule parsing (`parse_schedule`), next-run computation, cross-process
claiming (`claim_job_for_fire`), timezone/grace handling, per-job runtime pins,
pause/resume/trigger and a run document per fire. Reimplementing any of that
inside Projects would be a second engine with a fraction of the testing.

```
project.cadence = 'repeatable'
project.schedule = '0 9 * * mon'
project.host_profile = 'research'
                    │
                    │  create_job() in the HOST profile's cron store
                    ▼
   jobs.json entry  { id: 'proj_<slug>', schedule: …,
                      prompt: 'hermes projects run <slug> --trigger schedule',
                      workdir: project.primary_path }
                    │
                    ▼  the gateway's cron ticker fires it
   `hermes projects run <slug>` → open a project_runs row → instantiate the
   active playbook's steps as cards (§7) → return; the kanban dispatcher does
   the work from there.
```

Two consequences to implement deliberately:

1. **cron is profile-scoped** (`~/.hermes/profiles/<p>/cron/jobs.json`) while the
   project record is root-anchored. So the job lives in `host_profile`, and
   `host_profile` is therefore **required** for a repeatable project and must be
   a row in `project_profiles`. If the host profile is removed from the project,
   the schedule is paused and the project's health turns amber (§9) — it is not
   silently re-homed.
2. `cron_job_id` on the project and `project_id` in the job's metadata are two
   halves of one link. `hermes projects doctor` (§12) reports a project whose
   `cron_job_id` no longer resolves, because a broken schedule is invisible
   otherwise — it simply never runs.

**`next_run_at` is a display cache**, refreshed when the project is read; the
cron store stays authoritative. Never make a scheduling decision from it.

---

## 4. Autonomy — fully automatic, or asking for guidance

`autonomy` decides **how far the work advances without a human**, and it is
implemented entirely at seams that already exist in the board. The relevant
verified facts about the shipped dispatcher:

- Only `ready` cards with an **assignee** are dispatched (`_dispatch_once_locked`).
- `recompute_ready()` promotes `todo → ready` automatically once parents are
  `done`. So the gate a human can hold is the **`triage → todo`** transition,
  not `ready`.
- Irreversible actions are gated by FG-10 (`human_comms`, `reversible=False`
  never auto-answers — decision D6). Autonomy **cannot** waive that.

| level | who moves `triage → todo` | schedule may fire | checkpoints | irreversible acts |
|---|---|---|---|---|
| `manual` | human only | no — "Run now" only | every step is effectively a checkpoint | approval |
| `supervised` (**default**) | the run, for steps not marked `checkpoint` | yes | the playbook's `checkpoint` steps stop the run and ask | approval |
| `autonomous` | the run, for every step | yes | none | approval — **still** |

Additional caps, enforced **in the project's own promotion step** and never by
patching the shared dispatcher (which is board-global and used by the dashboard):

- `max_in_progress` — before promoting step cards, count this project's cards in
  `running` + `ready`; promote at most up to the cap. This is what stops a
  20-step playbook from spawning 20 workers on a 4-vCPU box.
- `budget_usd_per_run` — the run reads its own cost from the C8 ledger
  (`interactions` rows with `kind='cost'` for the run's `trace_id`); crossing the
  cap **stops promoting new cards**, sets `project_runs.status='waiting'`, and
  raises one `approval` ("this run has spent $X; continue?"). It never kills a
  card mid-flight — the shipped `max_runtime_seconds` per card already owns that.
- `require_approval` (JSON list of extra gates, e.g. `["outbound","spend"]`) —
  additive to D6, never subtractive.

**A `manual` project is a first-class, useful thing**, not a degraded one: the
project is then a place where everything about the work lives, and the human
does the work. Do not build any path that silently promotes a `manual` project's
cards.

---

## 5. Guidance — instructions, feedback and asks over time

This is the half of Leo's requirement that neither earlier document addressed:
*"it can require user to provide instructions, feedback and guidance over time."*

Three distinct mechanisms, deliberately not one table:

| the user… | mechanism | shows up in |
|---|---|---|
| gives a standing instruction ("always cc legal", "never touch prod before 6pm") | `project_directives(kind='directive')` | compiled into every future run's seed prompt; listed on the project page |
| judges what happened ("that reply was too formal") | `project_directives(kind='feedback', scope='run'\|'card', rating=…)` | shown on the run/card; the most recent feedback is compiled into the **next** run's prompt |
| is asked a question by the agent | FG-10 `human_comms` `kind='proactive_ask'` + a to-do (§10) | `/inbox`-style approval surfaces, the project page, and the To-dos badge |

### 5.1 When guidance takes effect: the next run, never mid-conversation

A directive is **compiled into the run's seed prompt** by
`hermes projects run` at spawn time, in a fixed order, and is never injected
into a running session. This is not a compromise; it is the same rule FG-29
adopted for goal-tier changes ("a tier change applies at the next session,
never mid-conversation") and it exists because the system prompt is frozen for
the life of a conversation and per-conversation caching is sacred.

The UI must say so, once, where a directive is added: *"applies from the next
run."* A product that pretends otherwise produces the worst bug class here — a
user who believes they have corrected the agent and has not.

To change a *running* card's behaviour there is exactly one path, and it already
exists: comment on the card (`task_comments`) or block it
(`kanban_block`/`kanban_unblock`). Guidance is for the method; a comment is for
this attempt.

### 5.2 The compiled guidance block

```
## Project: <name> (<slug>)
Purpose: <primary goal title> — <definition_of_done or "standing">
Cadence: repeatable, every Monday 09:00   ·   Run 14 of this project.

### Standing instructions (from <user>, newest first)
1. <directive body>            [added 2026-08-02]
2. …

### What we learnt last run
<the previous run's retro, or the most recent feedback with rating='bad'>
```

Hard rules, because this block is prompt bytes on every run:

- **Capped.** At most `PROJECT_GUIDANCE_MAX_DIRECTIVES` (default 20) active
  directives and `PROJECT_GUIDANCE_MAX_CHARS` (default 4000) compiled
  characters, both in `config.yaml` under `projects:` — **not** env vars
  (`AGENTS.md`: `.env` is secrets only). Adding directive 21 is refused with
  *"retire one first"*, mirroring FG-29's capped skill library: a monotonically
  growing instruction list is a tax on every run and, worse, becomes a place
  where contradictory instructions coexist unnoticed.
- **Ordered and dated**, newest first, so a later instruction visibly wins.
- **Attributed.** `author_user_id` is rendered; the agent must be able to tell
  whose instruction it is following.
- **Retire, never delete.** `active=0` + `retired_at`, so the record of what the
  agent was told when run 9 executed survives (that is the whole "reviewable"
  requirement).

### 5.3 When the agent needs guidance

The run raises `human_comms.create(kind='proactive_ask', dedupe_key='project-ask:<slug>:<run_no>:<n>')`
**and** a to-do (§10), then sets `project_runs.status='waiting'` and stops
promoting cards. The answer is written back as a
`project_directives(kind='directive', scope='run')` row so the answer is part of
the method, not just a settled notification — this is how a project accumulates
know-how instead of re-asking every month.

A `waiting` run that is not answered within
`projects.ask_timeout` (default 72h) is closed as `blocked` with an outcome
naming the unanswered ask. A run waiting forever is indistinguishable from a
broken one.

---

## 6. Runs — the unit of "it happened"

A **run** is one occurrence of the project doing work. It is what makes a
repeatable project reviewable and comparable.

```
trigger (schedule | manual | event | review)
   │
   ├─ open project_runs row (run_no = max+1), bind a C8 trace (bind_trace),
   │  status='running'
   ├─ compile the guidance block (§5.2)
   ├─ instantiate the active playbook's steps as cards (§7), respecting
   │  autonomy (§4) and max_in_progress
   ├─ optionally spawn ONE seeded session for the run itself (see below)
   ├─ the shipped kanban dispatcher executes the cards under host_profile
   └─ close: when every step card is done|archived, or a checkpoint waits, or
      the budget/ask stops it → status, outcome, summary, then the retro (§8)
```

**Who does the work: cards, or a session?** Both are supported and the
distinction is the playbook's:

- A step with `mode: 'card'` (default) becomes a board card, executed by a
  dispatcher-spawned worker under the assigned profile. Use for anything that
  may take a long time, be retried, or need its own worktree.
- A step with `mode: 'inline'` is executed by the run's own seeded session via
  `agent/seeded_session.spawn_seeded_session()` (the shared spawn path designed
  in ed.2 §1.2a for `/start`). Use for short, read-mostly work — a weekly digest
  that reads three sources and writes one summary should not cost three workers.

`spawn_seeded_session` is the **only** session-spawn path this feature may use.
If it has not landed yet (it lands with the To-dos `/start` step), Projects step 4
depends on it; do not write a second `AIAgent(...)` construction.

**Run status vocabulary is deliberately not the card vocabulary.** A run is
`running | waiting | blocked | done | failed | cancelled` — six words about an
occurrence. Cards keep their nine statuses. Do not merge the two.

**Cost is read, not stored.** `GET /{slug}/runs/{n}` sums `kind='cost'` rows for
the run's `trace_id`. If the ledger is unconfigured, cost renders as "not
recorded" — the C8 contract is fail-open and a run must never fail because
observability is off.

---

## 7. Repeatability — the playbook

The playbook is *how this project gets done*, versioned, so a repeat is a
re-execution of a known method rather than a fresh improvisation.

```jsonc
// project_playbook.steps
[
  { "key": "gather",  "title": "Collect this week's arrivals",
    "body": "…", "mode": "card", "assignee": "research",
    "depends_on": [], "checkpoint": false },
  { "key": "draft",   "title": "Draft the summary",
    "mode": "card", "assignee": "research", "depends_on": ["gather"] },
  { "key": "approve", "title": "Owner reviews the draft",
    "mode": "card", "depends_on": ["draft"], "checkpoint": true },
  { "key": "send",    "title": "Send to the list",
    "mode": "card", "depends_on": ["approve"] }
]
```

### 7.1 The sequencing engine already exists

Instantiation writes each step as a card with `project_id` set, and writes a
`task_links` parent/child row for every `depends_on`. That is all the sequencing
this feature needs, because `recompute_ready()` already promotes a `todo` card to
`ready` only when **all its parents are `done` or `archived`** — with a
worker-initiated-block exception and a consecutive-failure circuit breaker
already implemented. A playbook DAG is therefore executed by shipped, tested
code, and Projects writes zero scheduling logic.

Rules:

- Steps whose `depends_on` are satisfiable start in `todo` (or `triage` when
  `autonomy='manual'`); all others start in `todo` as well and are *held by their
  parent links*, which is exactly what the board is for. Nothing starts in
  `ready` and nothing may be created `running` (the store refuses it).
- A `checkpoint: true` step's **successors are created in `triage`**. Completing
  the checkpoint card raises an `approval` + a to-do; the human's "Continue"
  moves the successors to `todo`. No new status, no new engine.
- `assignee` must be a profile in `project_profiles`; a step with no assignee
  inherits `host_profile`. A card with no assignee is never dispatched, so a bad
  playbook stalls visibly instead of running under a surprise profile.
- **Cycle check at save time**, not at run time: reject a `steps` array whose
  `depends_on` graph is cyclic or references an unknown key, with the offending
  keys named. A cycle discovered at 09:00 on Monday is a silent no-run.

### 7.2 Revisions, and who may change the method

- `POST /{slug}/playbook` creates rev N+1 with `active=0`.
- `POST /{slug}/playbook/{rev}/activate` requires `lead`/`admin` and records
  `activated_at` + `note`. **A run pins its `playbook_rev`**, so a mid-flight run
  is unaffected by an activation and run 13 remains reproducible after rev 4
  lands.
- The agent may **propose** a revision (that is the learning loop, §8); it may
  never activate one. This is the same posture FG-29 took for skill promotion:
  the crossing is what needs a human.
- "Repeat this run" = a manual-trigger run on the **run's own** `playbook_rev`,
  not necessarily the active one, so "do exactly what worked last time" is
  expressible.

---

## 8. Review and learning

The requirement is *"everything is tracked properly … so that it can be
reviewed, repeated and learnt from over time."* Tracking is §6; repetition is
§7; learning is a three-step path where **every crossing is human-approved**.

### 8.1 The per-run retrospective

When a run closes, the run's session (or a short `spawn_seeded_session` call for
card-only runs) writes `project_runs.retro`: what was done, what deviated from
the playbook, what blocked, what it cost, and **at most three concrete proposals**.
Human-editable on the run page. A run with no retro is shown as such — it is a
gap in the record, not a blank.

Kept honest by construction: the retro is written from the run's own artefacts
(its cards, their `task_runs` outcomes, its `task_events`, its C8 trace), and the
prompt says so. A retro is not allowed to be a summary of intentions.

### 8.2 Three destinations for what a run learnt

| what was learnt | where it goes | who approves |
|---|---|---|
| "the method should change" | a **playbook revision** (`active=0`, `note` naming the run) | `lead`/`admin` activates (§7.2) |
| "this is a durable instruction" | a proposed `project_directives(kind='directive')` row, `active=0` | any member activates |
| "this is general know-how, beyond this project" | the **shipped skills loop** — `agent/background_review.py` distils a `SKILL.md` in the host profile; FG-29's two-stage promotion carries it to the shared tier | profile reviewer, then owner |

Nothing on this table is automatic, and the third row deliberately adds no
mechanism: Hermes' self-improvement loop already produces skills, FG-29 already
designed the crossing, and `skills.external_dirs` is already read-only to
autonomous curation. A project's contribution is *provenance* — the skill
candidate records the project and run it came from.

### 8.3 Comparing occurrences

`GET /{slug}/runs` returns run N, N-1, N-2 … with duration, card count, blocked
count, cost and outcome, and the project page renders them as one small table.
This is the cheapest possible "learnt from over time" and the one a user will
actually look at: *run 14 took twice as long and cost twice as much as run 13.*

Do not build charts in v1. A table of the last ten runs answers the question.

---

## 9. Health, and what a project owes the user

One derived `health` value per project, computed on read, never stored:

| health | when |
|---|---|
| `ok` | nothing below applies |
| `attention` | a card is `blocked`; a run is `waiting` on an ask or budget; a `standing` project is past `review_every`; a `one_off` project is past `due_at` |
| `stalled` | a `repeatable` project whose last run is older than two schedule periods; a project whose `cron_job_id` does not resolve; a project whose `host_profile` left `project_profiles` |

`stalled` exists because the failure mode of an automated project is **silence**,
and silence looks identical to success on a list page.

---

## 10. The seams with To-dos and Goals

**A project asks the user through the To-dos page.** When a project needs a
human — a blocked card, a `waiting` ask, a due review, a budget stop — it
creates one to-do via `todo_store.create(...)` in the **asking user's** profile
with `source_kind='project'`, `source_ref='<slug>:<run_no>'`, and a
`dedupe_key`-collapsing title, then links it back with
`project_links(kind='todo')`. The shipped notifier and badge then do their job.
No second notification system, and "what needs me next" stays one list.

**A to-do becomes a card** (ed.2 Part 2, unchanged and still authoritative):
`POST /{slug}/cards` takes an optional `from_todo: {profile, id}`; the to-do is
read under the caller's own principal, the card is created `status='triage'`
(never `ready` — promotion is not dispatch), a `project_links(kind='todo')` row
records provenance, and the to-do moves to `working` with a transition note
naming the card. **Human-only, one-way, no reverse sync, no `project_id` on
to-dos.**

**Goals.** `project_links(kind='goal')` is the join to FG-29. The project's
headline number is the **primary goal's metric progress**, with the card rollup
shown beside it (`3 running · 1 blocked · 8/19 done`) but never as the headline.
A project with no linked goal shows *"no goal set"* — not `0%`, and not a card
ratio quietly standing in for one.

---

## 11. Permissions and cross-profile reads

The board and the project store are SQLite with no RLS, so enforcement is
app-layer at **exactly one seam**: the new router. Five rules, tested as a
negative matrix:

1. Every endpoint resolves the C1 principal with
   `_comms_resolve_principal(request, allow_as=True)`, as `/todos` and `/inbox` do.
2. A caller may **read** a project when they are the owner, or hold a
   `project_members` row, or the project is `visibility='shared'` **and** the
   caller holds a `principals` row in a profile listed in `project_profiles`.
   Anything else is **404, not 403** — the existence of a project is itself
   information.
3. **Writes** need `lead` or an instance `admin`/`owner`; `viewer` never writes.
   Additionally: any member may create a project and add *links*; only
   `lead`/`admin` may add a **profile**, activate a **playbook**, change
   **autonomy**, or set a **schedule** — those four are the acts that spend
   tokens or change what the system will do unattended.
4. **Board reads stay principal-filtered.** Always pass
   `kanban_db.list_tasks(principal=…)`, never `None`. A project view must not
   become the way to read another user's `private:` card.
5. **A `project_links` row is a pointer, never an authority.** Resolving a link
   re-reads the object through the owning profile's own API under the *caller's*
   principal. An unresolvable link renders as its cached `label`, greyed, marked
   "you don't have access" — visible as a fact, unreadable as content.

Rule 5 is the load-bearing one: it is what lets a shared, RLS-less board carry
links into profile-scoped, RLS-protected data without becoming a hole in it.

**Cross-profile aggregation is a bounded fan-out, never a join** (FG-27): the
detail read fans out over `project_profiles` (typically 1–3) in parallel under
the caller's principal, and **degrades per profile** — one profile that is down
or that the caller is not enrolled in yields a panel section marked unavailable,
not a 500 for the page.

---

## 12. The API

New `hermes_cli/projects_api.py`, mounted in `web_server.py` beside the todos and
incomings routers, prefix `/api/registry/projects`. It calls `projects_db`,
`kanban_db`, `todo_store`, `cron.jobs` and `human_comms` **directly** — never the
dashboard kanban plugin over HTTP (that plugin sits behind the dashboard's own
auth and is not reachable from `agent-home`).

| endpoint | purpose |
|---|---|
| `GET /` | readable projects: filters `status`, `cadence`, `health`, `q`, `archived`; keyset `cursor` → `{items, next_cursor}`. Each item: goal progress, card rollup, member count, `cadence`, `next_run_at`, `health`, `due_at` |
| `POST /` | create: name, slug?, description, icon/colour, `cadence`, `definition_of_done`, `board_slug` (bind or create), `host_profile`, folders, first goal link |
| `GET /{slug}` | record + members + profiles + active playbook + active directives + board rollup + links grouped by kind + last N `task_events` + last 5 runs |
| `PATCH /{slug}` | name, description, status, cadence, `due_at`, `review_every`, icon/colour, visibility, `board_slug`, `definition_of_done`, `max_in_progress`, `budget_usd_per_run` |
| `PATCH /{slug}/autonomy` | `autonomy` + `require_approval` (separate route so the audit line and the permission check are unmistakable) |
| `PUT /{slug}/schedule`, `DELETE /{slug}/schedule` | create/update/pause the host profile's cron job; refuses without an active playbook |
| `POST /{slug}/members`, `DELETE /{slug}/members/{user_id}` | membership + role |
| `POST /{slug}/profiles`, `DELETE /{slug}/profiles/{profile}` | which instruments the project runs on |
| `POST /{slug}/links`, `DELETE /{slug}/links` | attach/detach a file, arrival, to-do, goal, memory doc, conversation or URL |
| `GET /{slug}/playbook`, `POST /{slug}/playbook`, `POST /{slug}/playbook/{rev}/activate` | the method and its revisions (cycle-checked on save) |
| `GET /{slug}/directives`, `POST /{slug}/directives`, `POST /{slug}/directives/{id}/retire` | guidance and feedback |
| `GET /{slug}/runs`, `GET /{slug}/runs/{n}` | the record; detail includes cards, cost from C8, retro |
| `POST /{slug}/runs` | start a run now (`trigger='manual'`, optional `playbook_rev` to repeat an old method) |
| `POST /{slug}/runs/{n}/continue` | pass a checkpoint / answer a budget stop |
| `POST /{slug}/runs/{n}/cancel` | stop promoting; archive this run's un-started cards; never kills a running worker |
| `POST /{slug}/runs/{n}/retro` | write or edit the retrospective |
| `GET /{slug}/board` | the project's columns — `list_tasks(project_id=…, principal=…)` plus the same rollups the dashboard computes, **through the shared helper** |
| `POST /{slug}/cards` | create a card carrying `project_id` (optionally `from_todo`, §10) |
| `PATCH /{slug}/cards/{task_id}`, `GET /{slug}/cards/{task_id}`, `POST /{slug}/cards/{task_id}/comments` | the shipped card behaviours, through `kanban_db` transitions (which still refuse a direct `running`) |
| `GET /{slug}/activity` | merged tail: `task_events` for the project's cards + C8 traces for its runs and linked sessions |
| `GET /{slug}/conversations` | sessions whose cards carry `project_id` (`tasks.session_id`) + run sessions + explicitly linked ones |
| `GET /{slug}/events?since=<event_id>` | the tail for live updates (long-poll/SSE via the BFF; `latest_event_id` comes from `/board`) |
| `POST /{slug}/summarise` | the rolling "where this stands"; writes `summary`/`summary_at` |

**One shared rollup helper.** Move `plugin_api.get_board`'s aggregation (link
counts, comment counts, child done/total, diagnostics, latest run summary) into
`hermes_cli/kanban_view.py` and have **both** routers call it. Copying 140 lines
of aggregation into a second surface is how two boards start disagreeing about
what "progress" means. This lands as its own PR, with the dashboard repointed
and no behaviour change.

---

## 13. The pages (`agent-home`)

Conventions, non-negotiable because `/todos` and `/inbox` settled them:
server-rendered first paint under the resolved principal, `data-component` on
every component root, filter state in the URL, `loading.tsx` skeletons,
`BusyRegion` around anything that mutates, and **`filters.ts` must be a
server-safe module, not `"use client"`** (the production-only server/client
boundary bug that already bit `/todos`; the boundary test in
`src/app/server-client-boundary.test.ts` is extended, not bypassed).

### `/projects` — the list

```
▣  Acme rollout            one-off · due Fri        ●●●○○ 62%   ok
   4 members · 2 profiles · 3 running, 1 blocked · goal: Land Q3 revenue
   ───────────────────────────────────────────────────────────────────────
↻  Weekly research digest  repeatable · next Mon 09:00 · run 14  supervised
   last run 6d ago · 20 min · $0.42 · ok
   ───────────────────────────────────────────────────────────────────────
∞  Inbox hygiene           standing · reviewed 21d ago          attention
```

Chips: Active · Repeatable · Standing · Attention · Paused · Done · All. The
cadence glyph (`▣` one-off, `↻` repeatable, `∞` standing) is the one piece of
information that changes what a user expects from a row, so it is the first
thing on it.

### `/projects/[slug]` — the one place

One scrollable page on a phone with a sticky segmented control; two columns from
`md:` up. **Panels, not tabs that hide things** — "see it all in one place" is
the requirement, and a tab is a place to hide a panel.

```
┌─ Header ─────────────────────────────────────────────────────────────────┐
│ ↻ Weekly research digest    repeatable · next Mon 09:00 · supervised [⋯] │
│ "Where this stands: run 14 waiting on your answer about the tone." (agent)│
│ [ Run now ]  [ Continue ]  ← only when a run is waiting                   │
└──────────────────────────────────────────────────────────────────────────┘
[Progress] [Board] [Runs] [Method] [Guidance] [People] [Files] [Resources] [Conversations]

Progress      primary goal + metric trend (FG-29 rollup); card rollup per
              column; blocked cards FIRST — a blocked card is the only thing
              on this page asking for a human right now.
Board         the project's cards; one column per screen on a phone with ‹ ›.
              Each card: assignee profile, run state, comments, child N/M,
              diagnostics badge. Tap → /projects/[slug]/cards/[id].
Runs          the last ten runs: no, trigger, when, duration, cost, outcome,
              retro present?; tap → the run page (its cards, its trace, its
              retro, "repeat this run").
Method        the active playbook: prose + the step DAG (an indented list, not
              a graph widget), its revision and who activated it; proposed
              revisions awaiting activation, with a diff against the active one.
Guidance      standing instructions newest-first with author and date, an
              "Add instruction" field that says "applies from the next run",
              retired instructions behind a disclosure, and the open ask when
              a run is waiting.
People        members (avatar, role) and participating profiles (name, what it
              is running now, which one is the host) in one list — "who is on
              this" means both.
Files         linked /files assets + every task_attachments blob on the
              project's cards, one grid. A file that arrived on WhatsApp and a
              file a worker attached are the same thing to whoever is looking.
Resources     linked arrivals (quoted, → /inbox/<id>), memory documents, linked
              to-dos with their stages, plain URLs; each row shows its profile.
Conversations run sessions + sessions that produced the project's cards +
              linked ones: title, when, message count, → /chat/<id>, plus the
              C8 trace tail.
```

**Adding to a project is a link, from both ends.** The detail page has an "Add"
sheet (search across files, arrivals, to-dos, goals, conversations), and
`/todos/[id]`, `/inbox/[id]` and the files detail each gain "Add to project" —
which is where the user actually is when they realise something belongs to a
project. The same sheet does *promotion* (§10), with one line of copy stating
the difference: **link** keeps it a to-do; **promote** makes it a card and moves
the to-do to `working`.

### Files

```
src/app/projects/page.tsx                      list, server-rendered
src/app/projects/loading.tsx
src/app/projects/[slug]/page.tsx               detail
src/app/projects/[slug]/loading.tsx
src/app/projects/[slug]/runs/[no]/page.tsx     one run
src/app/projects/[slug]/cards/[id]/page.tsx    one card
src/app/api/projects/**                        BFF mirror, one route per endpoint
src/components/projects/ProjectsList.tsx       + ProjectCard, ProjectsFilters
src/components/projects/ProjectDetailView.tsx  orchestrates the panels
src/components/projects/panels/{Progress,Board,Runs,Method,Guidance,People,Files,Resources,Conversations}Panel.tsx
src/components/projects/BoardColumn.tsx        + BoardCard, CardDetailView
src/components/projects/RunView.tsx            + RetroEditor
src/components/projects/PlaybookEditor.tsx     steps as a list; cycle errors inline
src/components/projects/AddToProjectSheet.tsx  reused by /todos, /inbox, /files
src/components/projects/filters.ts             URL codec — server-safe module
```

Client methods on `HermesApiClient`: `projects()`, `project()`, `createProject()`,
`updateProject()`, `setProjectAutonomy()`, `setProjectSchedule()`,
`projectBoard()`, `projectCard()`, `createProjectCard()`, `updateProjectCard()`,
`commentOnProjectCard()`, `projectRuns()`, `projectRun()`, `startProjectRun()`,
`continueProjectRun()`, `cancelProjectRun()`, `writeProjectRetro()`,
`projectPlaybook()`, `saveProjectPlaybook()`, `activateProjectPlaybook()`,
`projectDirectives()`, `addProjectDirective()`, `retireProjectDirective()`,
`projectMembers()`, `addProjectMember()`, `addProjectProfile()`,
`linkToProject()`, `unlinkFromProject()`, `projectActivity()`,
`projectConversations()`, `projectEvents()`; types in `src/types/index.ts`.

**Navigation:** secondary nav (sidebar + More sheet on a phone) plus a
first-class Home card listing active projects with their health and next run.
The primary bar was budgeted for To-dos on 2026-08-11 and re-litigating it
before Projects has any real data would be churn (ed.2 Q3).

---

## 14. The agent's route in (footprint rung 2)

Extend the existing `hermes project` command tree rather than forking it, and
add the run/method verbs:

```
hermes projects [--actor <user>] <verb> …          # --actor, the goal_tree_cmd convention

hermes projects list      [--status s] [--cadence c] [--health h] [--json]
hermes projects show      <slug> [--json]
hermes projects create    "<name>" [--cadence …] [--goal <id>] [--host-profile p]
hermes projects link      <slug> --kind file|arrival|todo|goal|memory|session|url
                                 --profile <p> --ref <id>
hermes projects members   <slug> [--add <user> --role r]
hermes projects cards     <slug> [--status s]
hermes projects card add  <slug> "<title>" [--assignee <profile>] [--from-todo <id>]
hermes projects playbook  <slug> [show|save <file.json>|activate <rev>]
hermes projects guidance  <slug> [list|add "<body>"|retire <id>]
hermes projects run       <slug> [--trigger schedule|manual|event|review]
                                 [--playbook-rev N] [--dry-run]
hermes projects runs      <slug> [--limit 10] [--json]
hermes projects retro     <slug> <run_no> [--write]
hermes projects summarise <slug>
hermes projects doctor    [--slug s]     # broken cron links, missing host profile,
                                         # stalled repeatables, unresolvable links
```

`hermes projects run` is the load-bearing verb: it is what the cron job calls,
and therefore the only place that compiles guidance, instantiates the playbook
and opens a run row. `--dry-run` prints the cards it *would* create and the
compiled guidance block — the single most useful thing a user can do before
turning a schedule on.

`skills/productivity/projects/SKILL.md` states the rules that keep this from
becoming noise:

1. **A project is for work that spans sittings, people or profiles.** One
   decision is a to-do; in-session planning is `tools/todo_tool.py`.
2. **Propose, don't create.** The agent may propose a project, a playbook
   revision or a directive; the human creates and activates.
3. **Link, never copy.** Everything a project gathers is a pointer.
4. **Never move a card past a checkpoint, never activate a playbook, never widen
   autonomy.** Those are human acts by design.
5. **Read the last run's retro before starting work** — that is what the record
   is for.

No new core model tool.

---

## 15. Failure modes designed against

1. **Silent automation.** A repeatable project that stops firing looks identical
   to one with nothing to do. → `health='stalled'`, `doctor`, and the
   `cron_job_id` round-trip check.
2. **Guidance that isn't followed.** A user adds an instruction mid-run and
   believes it applied. → one sentence of copy, `applied_from_run` on the record,
   and the run page showing exactly which directives its prompt carried.
3. **Instruction sprawl.** 60 contradictory directives, each individually
   reasonable, taxing every run. → the cap, "retire one first", and dated
   newest-first ordering so the winner is visible.
4. **Runaway cost.** An autonomous 20-step playbook on a 4-vCPU box. →
   `max_in_progress` at the project's own promotion step, `budget_usd_per_run`,
   the shipped per-card `max_runtime_seconds` and consecutive-failure breaker.
5. **A second engine by accident.** The tempting mistakes are: a Projects
   scheduler (use cron), a Projects workflow router (use parent links +
   `recompute_ready`), a Projects notification store (use FG-10 + to-dos), a
   Projects rollup (use `kanban_view`). Each one is called out at its section.
6. **The board and the project page disagreeing.** One aggregation helper; never
   store a derived count.
7. **Link rot.** A pointer whose target was deleted renders from its cached
   `label` as "no longer available", never as an error; the digest cron drops
   pointers unresolvable for 30 days.
8. **The permission seam being bypassed.** The only defence is that there is one
   router; the negative matrix in §16 is what keeps that true.
9. **Retro theatre.** A retro that summarises intentions instead of artefacts
   teaches nothing. → written from cards, `task_runs`, events and the trace, and
   capped at three concrete proposals.

---

## 16. Testing

Behaviour contracts, not change detectors (`AGENTS.md`).

**Store**
- The root migration is idempotent; imports per-profile rows with `-2` slug
  suffixes and `imported_from_profile` set; an existing `projects.db` opens
  unchanged; every additive column defaults so old rows read back identically.
- `HERMES_PROJECTS_DB` overrides the root resolution (the reversibility path).

**Cadence / schedule**
- `repeatable` without an active playbook is refused with 409 naming the
  playbook; with one, `PUT /schedule` creates exactly one cron job in the host
  profile and stores `cron_job_id`.
- `repeatable → one_off` pauses the job and never deletes it.
- Removing the host profile from `project_profiles` pauses the schedule and turns
  health `stalled`; `doctor` reports both.
- `next_run_at` is only ever a cache: deleting it changes no scheduling decision.

**Autonomy**
- `manual`: a run creates cards in `triage` and **nothing is ever promoted**
  without a human; assert no card reaches `ready`.
- `supervised`: non-checkpoint steps promote; a `checkpoint` step's successors
  stay `triage` until `POST /runs/{n}/continue`.
- `autonomous`: all steps promote — **and an irreversible action still raises an
  approval** (the D6 regression that would matter most).
- `max_in_progress=1` on a 5-step playbook: at most one card in `running|ready`
  at a time; the rest wait; the dispatcher is not patched.
- `budget_usd_per_run` crossed → run `waiting`, one approval raised, no card
  killed mid-flight, and promotion resumes on approval.

**Guidance**
- A directive added while a run is in flight does **not** appear in that run's
  compiled block and **does** appear in the next one (the property the copy
  promises).
- The compiled block is capped by count and characters; directive 21 is refused;
  order is newest-first with author and date.
- Retire sets `active=0` + `retired_at` and never deletes; a run's page still
  shows the directives it actually carried.
- An unanswered `proactive_ask` past `ask_timeout` closes the run `blocked` with
  an outcome naming the ask; the answer is written back as a directive.

**Runs / playbook**
- `depends_on` becomes `task_links`; a child is not `ready` until its parent is
  `done` (assert through the project endpoint, not just the store).
- A cyclic or dangling `steps` array is rejected **at save** with the offending
  keys named.
- A run pins `playbook_rev`; activating rev N+1 mid-run does not change the
  running one; "repeat run 13" runs rev 13's method.
- Cancelling a run archives its un-started cards and leaves running workers
  alone.
- Cost renders "not recorded" when the C8 ledger is unconfigured; the run still
  completes (fail-open).

**Permissions (negative matrix)**
- owner / `lead` / `member` / `viewer` / non-member / member-of-a-different-project
  × read, link, add-profile, activate-playbook, change-autonomy, set-schedule.
  Non-member reads get **404**.
- `list_tasks(principal=…)` still hides another user's `private:` card *through
  the project endpoint*.
- A link to a file the caller cannot read renders greyed from `label` and leaks
  no content; a link into a profile the caller is not enrolled in is
  "unavailable", not a 500.
- Fan-out: one profile down → that panel section is unavailable and the rest of
  the page renders.

**Promotion seam (ed.2 Part 2)**
- Promoting creates a `status='triage'` card with `project_id`, writes the
  `project_links` row, and moves the to-do to `working` — not `done`.
- A to-do the caller cannot read in the named profile → 404 **and no card**.
- Promoting the same to-do twice is refused by the `project_links` primary key
  rather than making a second card.

**Frontend**
- The list defaults to active; URL filter state round-trips; the detail page
  renders every panel from one fetch; `filters.ts` passes the server/client
  boundary test; nav tests updated for the secondary slot and the Home card.

**Live (systest box), after deploy**
- Create a repeatable project with a 4-step playbook including a checkpoint, set
  a schedule 5 minutes out, and watch: the cron ticker fires
  `hermes projects run`, the dispatcher executes step 1 under the host profile,
  the checkpoint holds, "Continue" releases the rest, the run closes with a
  retro, and a second principal who is not a member gets a 404 for the whole
  project.

---

## 17. Sequencing (one PR each, against `develop`)

| # | scope | notes |
|---|---|---|
| 1 | **Store**: root-anchored `projects_db` + import migration + `project_members`, `project_playbook`, `project_directives`, `project_runs`, `project_run_cards` + the additive columns; `list_tasks(project_id=…)` + its index; **repoint the Electron app and `hermes project`** | Nothing else uses it yet. The repoint rides along (ed.2 Q5) because two live stores diverge daily. |
| 2 | **`kanban_view.py`**: extract `plugin_api.get_board`'s aggregation, repoint the dashboard | Pure refactor, no behaviour change, landed alone so a regression is unambiguous. |
| 3 | **API part 1**: `projects_api.py` — record, members, profiles, links, board, cards, the permission matrix, the fan-out | Backend only. |
| 4 | **Runs + playbook + guidance**: the run lifecycle, playbook instantiation via parent links, the compiled guidance block, checkpoints, caps, budget | Depends on `agent/seeded_session.spawn_seeded_session()` (To-dos ed.2 step 8) for `mode:'inline'`; card-only runs do not. |
| 5 | **Schedule**: `PUT/DELETE /schedule` wiring the host profile's cron job, `next_run_at`, `health`, `doctor` | |
| 6 | **BFF + client + types** in `agent-home` | |
| 7 | **List page** + Home card + nav slot | |
| 8 | **Detail page**: all nine panels, the card route, the run route, `AddToProjectSheet` + "Add to project" on `/todos`, `/inbox`, `/files` | |
| 8b | **Promotion**: `from_todo` on `POST /{slug}/cards`, the `/todos/[id]` promote action, the link-vs-promote copy | A promote button with no project page to land on is a trapdoor, so it follows 8. |
| 9 | **CLI + skill**: `hermes projects …`, `skills/productivity/projects/SKILL.md` | The agent's route in. |
| 10 | **Retro + learning**: retro write-back, proposed playbook revisions and directives, skill-candidate provenance into `background_review` | Last, because it needs real runs to be worth anything. |
| 11 | **Events + summary**: `GET /{slug}/events?since=`, `POST /{slug}/summarise` | |

Steps 1–5 are backend-only and land first, so that by the time step 8 renders a
panel there is a real project with real runs behind it — the sequencing that
worked for Incomings and To-dos.

### What this document changes relative to ed.1 / ed.2

- **Adds** cadence, autonomy + caps + budget, guidance (directives/feedback/asks),
  runs, the playbook, retros and the learning path, health, and the cron binding.
  None of that existed in either document.
- **Adds** `host_profile` as a required field for a repeatable project, and the
  `project_members` / `project_playbook` / `project_directives` / `project_runs` /
  `project_run_cards` tables.
- **Keeps unchanged**: the root-store decision, the "no second engine" posture,
  the permission model and its five rules, the fan-out, the shared rollup helper,
  panels-not-tabs, secondary nav + Home card, goal-metric progress as the
  headline number, and the to-do → card promotion seam.
- **Supersedes** ed.1's §9 sequencing (superseded by §17 above) and ed.1 §11
  (answered in ed.2, restated here).

---

## 18. Decisions taken here

1. **Cadence is a first-class commitment** (`one_off | repeatable | standing`),
   not a tag, and it decides what drives the project.
2. **A repeatable project's schedule is a `hermes cron` job in its host
   profile.** No second scheduler. `host_profile` is therefore required, and the
   two-way link is checked by `doctor`.
3. **Autonomy is three levels implemented at existing seams** — the
   `triage → todo` gate, assignee presence, and FG-10 approvals — with
   `max_in_progress` and `budget_usd_per_run` enforced in the project's own
   promotion step, never by patching the shared dispatcher.
4. **Irreversible actions are always approval-gated.** `autonomous` widens what
   proceeds unattended; it never waives D6.
5. **Guidance is durable, attributed, capped, retired-not-deleted, and applies
   from the next run** — because the system prompt is frozen for a
   conversation's life and prompt caching is sacred. The UI says so.
6. **The playbook is versioned card templates executed by parent links.** No
   workflow engine: `recompute_ready()` already is one. `workflow_template_id`
   stays untouched.
7. **A run is the unit of the record**, with its own six-word status vocabulary,
   its own C8 trace, and cost read from the ledger rather than stored.
8. **Learning is three human-approved crossings** — playbook revision, durable
   directive, and the shipped `background_review` → FG-29 skill promotion. The
   agent proposes; the human activates.
9. **A project asks the user through To-dos and FG-10**, never through a new
   notification system.
10. **Projects depends on the board, to-dos and goals; none of them learn about
    Projects.** No `run_id` on `tasks`, no `project_id` on to-dos.

---

## 19. Open questions for the owner

Each has a **recommended default** an implementer can proceed on, plus the cost
of changing it later. None blocks implementation.

| # | question | recommended default | cost of changing |
|---|---|---|---|
| 1 | **Who may turn on automation?** Should setting a schedule or widening `autonomy` to `autonomous` require the instance owner, or is a project `lead` enough? | **`lead` sets a schedule; only `owner`/`admin` sets `autonomous`.** Unattended, unsupervised spend is an instance-level decision. | Free — both are checks at one router seam. |
| 2 | **Default autonomy for a new project.** | **`supervised`.** A new project's first run should be watched, and the upgrade is one tap. | Free. |
| 3 | **Budget unit.** Is `budget_usd_per_run` the right cap, or do you want wall-clock/card-count caps instead (cost depends on the C8 ledger being configured)? | **Cost per run, with `max_in_progress` as the always-available cap** — so a deployment with no ledger is still protected. | Free; both come from the same read. |
| 4 | **Standing-project review.** Should an elapsed `review_every` raise a to-do only, or also start a `review` run that prepares the review? | **A to-do plus a `review`-triggered run that only *prepares* (reads, summarises, proposes) and never acts.** | Cheap — it is a trigger value and a playbook. |
| 5 | **One project → one board?** | **One board per project**, created with the project's slug; `board_slug` stays settable so an existing board can be adopted, and `tasks.project_id` keeps sharing technically fine. | Free — a default in the create path plus a sentence of copy. |
| 6 | **Does a repeatable run keep its cards, or archive them?** After run 14 closes, run 15 creates fresh cards from the same steps; do run 14's cards stay on the board? | **Archive run N's `done` cards when run N+1 opens**, keeping them queryable through `project_run_cards` and the run page. A board that accumulates 52 copies of "Draft the summary" is unreadable. | Cheap; it is one sweep at run open. |
| 7 | **Do you want a fourth autonomy level** — "agent may also *propose* new steps mid-run" (self-extending playbook)? | **No, not in v1.** A run that can add its own work is a different risk class and the retro path already gives you the same capability with a human in it. | Additive later; the retro loop is the seam it would use. |
| 8 | **The field list.** Your message referenced "the key information for each project" but the list did not arrive. §1.1 is my reconstruction. | **Reconcile §1.1 against your list before step 1 lands** — the store PR is the cheapest place to add a column and the most expensive place to have missed one. | A column addition is cheap; a *concept* addition after the API and page exist is not. |
