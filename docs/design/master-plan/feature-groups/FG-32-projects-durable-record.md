# FG-32 — Projects: the durable record of a piece of work

**Wave:** P7-A (after FG-27/FG-28/FG-29; needs no other FG to start) · **Owner agent:** devin (design), devin (implementation) · **Status:** IMPLEMENTED — steps 1–11 are merged into `develop` (#251 store, #252 kanban rollup, #254 API, #258 runs, #259 schedule, #261 BFF, #263 list, #266 detail, #279/#280 8b + live, #271 CLI + skill, #275 score, #276 learning + events). The design is frozen at ed.3.2; §1.1 is the owner's fifteen-field list verbatim. **Open defects are recorded in §20** — the review found 17, none of them fixed at the time of writing

## Provenance

This FG began life as a standalone implementation design
(this document (FG-32), ed.1 → ed.3.2) so that an agent with
no other context could build it; it is recorded here, unchanged in substance,
because Projects is a feature group of the same weight as the rest of the plan
and its record belongs beside them.

**Origin — Leo, 2026-08-13:**

> "a project stores all the related information about a project which can be
> long lasting, repeatable or one-time only. It can be fully automatic or it
> can require user to provide instructions, feedback and guidance over time.
> Everything is tracked properly in Project so that it can be reviewed,
> repeated and learnt from over time."

**The fifteen fields — Leo, 2026-08-14** (M = mandatory, O = optional):
1 Goal (short title) M · 2 Requirements/Description (long) M · 3 Outputs M ·
4 Participants M · 5 Progress M · 6 Target audience O · 7 Score O ·
8 Samples/References O · 9 Plan O · 10 Contacts O · 11 Files O · 12 Memories O ·
13 Tools O · 14 Skills O · 15 Conversation Histories O.
**§1.1 is that list, verbatim, mapped to storage**; every other section exists to
serve it.

**Consolidates** (both superseded for implementation by this document):
`docs/plans/2026-08-12-001-projects-page-plan.md` (ed.1 — the substrate review,
the record, the page) and
`docs/plans/2026-08-13-001-todos-and-projects-design-revision.md` (ed.2 —
Projects' six open questions answered; the to-do → card seam).

**Extends those with** cadence (one-off / repeatable / standing), autonomy
(manual / supervised / autonomous) and its caps, guidance (durable directives +
feedback, and when they take effect), and runs / retros / the learning loop.

**Editions:** ed.1 2026-08-13 · ed.2 · ed.3 (owner's 15-field rebuild, #242) ·
ed.3.2 2026-08-14 (goal split from name, samples/references optional, #245) ·
recorded as FG-32 2026-08-17.

## Reuse map

| anchor | what it already provides |
|---|---|
| `hermes_cli/kanban_db.py` | the execution substrate — statuses, task links, runs, events, attachments |
| `hermes_cli/projects_db.py` | the Project record; already carried `board_slug`, `project_profiles`, `project_links` |
| `hermes_cli/todo_store.py` | "what needs me next" — a project asks through it rather than inventing an inbox |
| `hermes_cli/human_comms.py` | FG-10 approvals and proactive asks (`approval` \| `proactive_ask`) |
| `cron/jobs.py`, `cron/scheduler.py` | the shipped, profile-scoped schedule engine a repeatable project uses — Projects adds no scheduler |
| `agent/background_review.py` | the shipped skill-distillation loop the learning path ends in |
| `hermes_cli/interactions.py` | C8 trace ledger; `kind='cost'` is where a run's cost comes from |
| FG-27 | profile-scoped datastore isolation — why project links are pointers, never copies |
| FG-28 | multi-profile administration — the host-profile model a run executes under |
| FG-29 | goal tree + skill promotion — the two destinations a retro's learning may cross into |


## 0. How to read this document

This is the **single file an implementer needs** for Projects. It restates the
parts of ed.1/ed.2 an implementer cannot work without, and adds the four
dimensions those documents do not cover (cadence, autonomy, guidance, runs and
learning). Where it disagrees with ed.1/ed.2, **this document wins** — the
diffs are listed in §17.

**Read §1.1 first.** It is the owner's fifteen-field list mapped to storage, and
it is the acceptance criterion for this feature: a Project that cannot hold all
fifteen is not this Project. Every later section exists to make one of those
fields work — the five mandatory ones especially (Goal, Requirements, Outputs,
Participants, Progress), because they are what a project *is*, and the ten
optional ones must each be genuinely omittable without breaking a page, a run or
a prompt.

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

A Project is five things at once, and the design keeps them separate on purpose.
`[n]` is the owner's field number from §1.1:

```
  ┌─ the commitment ────────────────────────────────────────────────────────┐
  │ [1] goal · [2] requirements · [3] outputs · [6] target audience         │
  │ cadence (one-off | repeatable | standing) · autonomy · caps and budget   │
  └─────────────────────────────────────────────────────────────────────────┘
  ┌─ the method ────────────────────────────────────────────────────────────┐
  │ [9] plan (the versioned playbook) · [13] tools · [14] skills ·           │
  │ [8] samples/references · the guidance given over time (directives,      │
  │ run feedback)                                                           │
  └─────────────────────────────────────────────────────────────────────────┘
  ┌─ the cast ──────────────────────────────────────────────────────────────┐
  │ [4] participants — people (members) AND profiles (instruments) ·        │
  │ [10] contacts — people the work involves who do not use this box        │
  └─────────────────────────────────────────────────────────────────────────┘
  ┌─ the work ──────────────────────────────────────────────────────────────┐
  │ kanban cards (shipped: statuses, parent links, runs, comments,          │
  │ attachments, events, circuit breaker) grouped by tasks.project_id       │
  └─────────────────────────────────────────────────────────────────────────┘
  ┌─ the record ────────────────────────────────────────────────────────────┐
  │ [5] progress · [7] score · runs (one row per occurrence) · retros ·     │
  │ [11] files · [12] memories · [15] conversation histories · C8 traces    │
  └─────────────────────────────────────────────────────────────────────────┘
```

Reading those five groups in order is also the order of the detail page (§13)
and of the compiled run prompt (§5.2). That is not a coincidence: *what are we
doing, how, with whom, what happened, how well* is both what a human opens the
page to find out and what a run needs told before it starts.

**The method is the part that makes a project repeatable, and the record is the
part that makes it learnable.** Everything under "the work" already exists and
is already tested; this design adds no execution engine, no second board, no
second scheduler and no second status vocabulary.

### 1.1 The key information a Project holds — the owner's field list

This is the authoritative field inventory. **M = mandatory** (the API refuses to
create or activate a project without it), **O = optional** (must be genuinely
absent-able: no page, run or prompt may assume it exists). "Where" names the
column, table or section that implements it.

| # | field | M/O | where it lives | notes |
|---|---|---|---|---|
| 1 | **Goal** (short title) | **M** | `projects.goal` (short, ≤160 chars) — **separate from `projects.name`** — + optional `project_links(kind='goal')` to the FG-29 goal tree | The outcome, as one sentence: *"Acme is live on prod with the team trained."* `name` is the short **label** ("Acme rollout") that fits a list row, a board title and a slug; `goal` is what success *is*, and it is what runs are told (§5.2). Owner decision, 2026-08-14: these are two fields. A linked goal *object* stays optional. §10 |
| 2 | **Requirements / Description** (long) | **M** | `projects.description` (long markdown) | The brief. Compiled into every run prompt (§5.2), so it is written *for the agent as much as for a human*. Empty-string creates are refused. |
| 3 | **Outputs** | **M** | `project_outputs` (§2.2) — one row per expected deliverable, each optionally satisfied by a link, card or file | The deliverables, declared before the work. At least one required. This is what makes progress and "done" mean something (§9.1). |
| 4 | **Participants** | **M** | `project_members` (people) + `project_profiles` (profiles/instruments, one flagged `host`) | Two lists, one field, because on this box a participant may be a person *or* an agent profile. Minimum: the owner + one profile. §2.2, §11 |
| 5 | **Progress** | **M** | **derived on read**, never stored — the ladder in §9.1 | Mandatory to *show*, impossible to *store* correctly: it is computed from outputs delivered, the primary goal's metric, and the card rollup, in that order. |
| 6 | **Target audience** | O | `projects.target_audience` (short text) | Who the outputs are *for*. Compiled into the run prompt because it is the field that most changes tone, format and depth of an output — and the one a user most resents re-stating each run. |
| 7 | **Score** | O | `project_runs.score_user` / `score_self` + `projects.score_rubric`; project score is **derived** (mean of the last 5 `score_user`) | How well it went, 1–5. `score_user` is the human's; `score_self` is the run's own claim in its retro. Divergence between them is the single most useful learning signal (§8). |
| 8 | **Samples / References** | **O** (owner-confirmed, 2026-08-14) | `project_links(kind='sample'\|'reference')` | A *sample* is "make it look like this" (an exemplar output); a *reference* is "this is the source material". Both are pointers (files, URLs, arrivals, past outputs). Samples are named in the run prompt; references are listed for the run to open on demand. |
| 9 | **Plan** | O | `project_playbook` (§7) — `body` (prose plan) and/or `steps` (ordered card templates) | The plan and the playbook are the same object: prose for a one-off, steps when it should be executable or repeatable. A `repeatable` project's plan is mandatory *for it* (§3.1) — that is a cadence rule, not a field rule. |
| 10 | **Contacts** | O | `project_contacts` (§2.2) | People the work involves who are **not** users of this box: a client, a reviewer, a mailing list. Distinct from participants because a contact has no principal, no permissions, and may be an outbound address. |
| 11 | **Files** | O | `project_links(kind='file')` + `task_attachments` on the project's cards, presented as one grid (§13) | Links, never copies. A file that arrived on WhatsApp and a file a worker attached are the same thing to whoever is looking. |
| 12 | **Memories** | O | `project_links(kind='memory')` → a memory document in a named profile | Pointer + `profile`; resolved under the caller's principal (§11 rule 5). A project curates *which* memories matter to it; it never owns them. |
| 13 | **Tools** | O | `projects.toolsets` (JSON list) | The toolsets a run is started with. **A subset filter, never a grant** — it can only narrow what the host profile already allows (§4.1). Applied at spawn, so it is frozen for the run's conversation. |
| 14 | **Skills** | O | `projects.skills` (JSON list of skill names) | Skills preloaded into the run's seed. Same subset rule, plus a count cap, because skills are prompt bytes on every turn (§4.1). |
| 15 | **Conversation Histories** | O | `project_links(kind='session')` + derived: sessions of the project's cards (`tasks.session_id`) and of its runs (`project_runs.session_id`) | Mostly *automatic* — you should not have to link the conversation that did the work. Explicit links exist for the chat that *started* it, before the project did. |

Fields this design adds beyond the list, each because a behaviour needs it (none
mandatory, all defaulted): `cadence`, `schedule`, `review_every`, `autonomy`,
`max_in_progress`, `budget_usd_per_run`, `definition_of_done`, `due_at`,
`status`, `visibility`, `host_profile`, `summary`, `health`, and `name` (the short
label that field [1] is *not*). Cadence and autonomy
are the two that carry the rest of the owner's brief ("long lasting, repeatable
or one-time only… fully automatic or… requires guidance"), so they are
first-class (§3, §4).

Three rules an implementer must not soften:

- **Derived numbers are never stored.** Progress, score, card counts, cost,
  health, "3 running · 1 blocked" — all computed on read from the outputs table,
  the goal tree, the board and the C8 ledger. A stored count is how two surfaces
  start disagreeing about the same project.
- **Optional means optional.** Every panel, every prompt section and every list
  row must render correctly with fields 6–15 empty. The commonest way to break
  this is a run prompt that says "Target audience: None".
- **Mandatory is checked at the API, not just the UI**, and for fields 1–4 at
  *create*; field 5 is derived so it cannot be missing.

### 1.2 What a Project is not

- **Not a to-do.** A to-do is one decision for a human. A project is a piece of
  work that spans sittings, people or profiles. The seam between them is §10.
- **Not a second board.** A project's work *is* kanban cards.
- **Not a second scheduler.** A repeatable project's schedule *is* a `hermes cron`
  job in its host profile (§3.2).
- **Not a chat log.** A conversation is linked, not owned; guidance the user
  wants to persist is a directive (§5), not a message in scrollback.
- **Not an address book.** `project_contacts` holds the people *this* project
  deals with. There is no global contacts store behind it and this design does
  not start one; a contact is a row on a project, and the same person appearing
  on two projects is two rows (§2.2).

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

-- ── [10] contacts: people involved who are not users of this box ─────────
-- No principal, no permissions, no global address book. `address` may be an
-- outbound destination (email, handle) and is therefore treated as PII: it is
-- returned to members only, never to a `viewer`, and never compiled into a run
-- prompt unless the step needs it.
CREATE TABLE IF NOT EXISTS project_contacts (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    role        TEXT,                       -- 'client', 'reviewer', 'the list' …
    org         TEXT,
    platform    TEXT,                       -- email | telegram | slack | phone | …
    address     TEXT,                       -- PII; see the note above
    user_id     TEXT,                       -- set when the contact also has a principal
    notes       TEXT,
    created_by  TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_contacts_project
    ON project_contacts(project_id);

-- ── [3] outputs: the deliverables, declared before the work ──────────────
-- Mandatory: a project has at least one row. This table is what gives progress
-- (§9.1) a denominator that means something, and it is the reason a project can
-- be "80% of cards done" and still honestly show 0% delivered.
CREATE TABLE IF NOT EXISTS project_outputs (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,          -- display order
    title         TEXT NOT NULL,             -- "The Monday digest email"
    spec          TEXT,                      -- what "good" looks like; free text
    kind          TEXT NOT NULL DEFAULT 'artifact',
                                             -- artifact | file | message | decision | report | code
    required      INTEGER NOT NULL DEFAULT 1,
    recurring     INTEGER NOT NULL DEFAULT 0, -- delivered once per run (repeatable projects)
    status        TEXT NOT NULL DEFAULT 'pending',
                                             -- pending | in_progress | delivered | accepted | dropped
    delivered_at  INTEGER,
    accepted_at   INTEGER,                   -- a human accepted it; only a human may
    accepted_by   TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_outputs_project
    ON project_outputs(project_id, seq);

-- One row per *delivery* of an output: which run produced it and what the
-- artefact is. `recurring` outputs accumulate one row per run, which is exactly
-- the "did last Monday's digest actually go out?" question.
CREATE TABLE IF NOT EXISTS project_output_deliveries (
    id           TEXT PRIMARY KEY,
    output_id    TEXT NOT NULL REFERENCES project_outputs(id) ON DELETE CASCADE,
    run_id       TEXT,                       -- project_runs.id, when a run produced it
    task_id      TEXT,                       -- the card that produced it, when any
    link_kind    TEXT,                       -- file | url | attachment | session | memory
    link_ref     TEXT,                       -- the pointer, resolved per §11 rule 5
    profile      TEXT,                       -- which profile owns the referent
    label        TEXT,                       -- cached display label (link rot, §15)
    note         TEXT,
    delivered_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_output_deliveries_output
    ON project_output_deliveries(output_id, delivered_at DESC);

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
    score_self    INTEGER,           -- [7] 1–5, the run's own claim, written with the retro
    score_user    INTEGER,           -- [7] 1–5, the human's; only a human may write it
    score_note    TEXT,              -- why that score (the part worth reading)
    scored_by     TEXT,
    scored_at     INTEGER,
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
ALTER TABLE projects ADD COLUMN goal                  TEXT;    -- [1] the outcome sentence, ≤160 (§2.2)
                                  -- NOT the existing `name`, which is the short label
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
ALTER TABLE projects ADD COLUMN definition_of_done    TEXT;    -- prose; defaults to "all required outputs accepted"
ALTER TABLE projects ADD COLUMN target_audience       TEXT;    -- [6]                            (§5.2)
ALTER TABLE projects ADD COLUMN score_rubric          TEXT;    -- [7] what 5 means here          (§8.4)
ALTER TABLE projects ADD COLUMN toolsets              TEXT;    -- [13] JSON list; a NARROWING filter (§4.1)
ALTER TABLE projects ADD COLUMN skills                TEXT;    -- [14] JSON list of skill names     (§4.1)
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

**Constraints the store enforces** (not just the router, so the CLI cannot
bypass them):

- `goal` non-empty, ≤160 chars — field [1]. One sentence: a store that accepts a
  paragraph here has re-invented `description`, and a page cannot render it.
- `name` non-empty, ≤60 chars — the label, not field [1]. It is what a list row, a
  board title, a to-do and a slug show, so it must be short and stable even when
  the goal is re-worded. `POST /` **defaults it** to the goal's leading clause
  (first `—`, `:` or `.`, else the first six words, truncated) so a caller never
  has to invent two strings; it is separately editable afterwards.
- `description` non-empty — field [2]. A project with no brief is a folder.
- At least one `project_outputs` row before `status` may leave `planning`, and
  before a schedule may be set — field [3]. Declaring the deliverable after
  automating its production is how you get a run that succeeds at nothing.
- At least one `project_members` row (the creator, as `lead`) and one
  `project_profiles` row (flagged `host`) — field [4].
- `toolsets` / `skills`, when present, are JSON arrays of strings; unknown names
  are rejected at write time with the unknown name quoted (§4.1), because a
  typo'd toolset that silently does nothing is worse than an error.

`project_links.kind` gains `sample`, `reference` and `memory` to the existing
`file | arrival | todo | goal | session | url` set (fields [8], [12], [15]);
`kind` stays a plain string column, and an unknown kind is refused by the router
rather than by a CHECK constraint, so adding one later is not a migration.

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
   the schedule is paused and the project's health turns amber (§9.2) — it is not
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

### 4.1 Tools [13] and skills [14] — a narrowing filter, never a grant

A project may say *which* toolsets its runs start with and *which* skills are
preloaded. Both are applied by `hermes projects run` when it spawns the run, and
both are therefore frozen for that run's conversation — the same
prompt-cache-shaped rule as guidance (§5.1).

Two properties are load-bearing, and inverting either turns a convenience into a
privilege-escalation path:

1. **Intersection, not union.** The effective toolset is
   `host_profile's enabled toolsets ∩ projects.toolsets`. A project can say
   "this run only needs `research`" — it can **never** say "this run also gets
   `terminal`" on a profile where terminal is off. A name in `projects.toolsets`
   that the host profile does not enable is dropped, and the drop is recorded on
   the run (`project_runs.summary` prelude + a `task_events`-style line), because
   a silently narrower run looks like a broken agent.
2. **Skills are prompt bytes.** Preloaded skills are capped
   (`projects.max_skills`, default 5, in `config.yaml` under `projects:` — not an
   env var) and resolved through the shipped skills loader in the host profile,
   including `skills.external_dirs`. A project may not define a skill inline;
   that is what `skills/` and the promotion loop in §8.2 are for.

Leaving both empty is the normal case and means "whatever the host profile
normally does". This is why they are field [13]/[14] optional: a project that
does not care about its instruments should not have to describe them.

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

The block is assembled in this fixed order, and every optional field is omitted
**with its heading** when empty (never rendered as "None"):

```
## Project: <name>                                   ← the short label
Goal: <goal>                                        ← [1] the outcome, one sentence
Run 14 · repeatable, every Monday 09:00 · autonomy: supervised
Definition of done: <definition_of_done or "all required outputs accepted">

### Brief                                            ← [2] requirements/description
<description, verbatim; truncated at PROJECT_BRIEF_MAX_CHARS with a pointer to
 `hermes projects show <slug>` for the rest>

### Audience                                         ← [6] omitted when unset
<target_audience>

### Outputs expected of this run                     ← [3]
- [ ] The Monday digest email — <spec>               (required, recurring)
- [x] Distribution list confirmed                    (delivered run 11)

### Samples to match                                 ← [8] samples only; references
- <label> → <path/url>                                 are listed, not inlined

### Standing instructions (newest first)             ← §5 directives
1. <body>                        [<author>, 2026-08-02]
2. …

### What we learnt last run                          ← §8
<run 13's retro, plus its score: user 3/5 — "too formal">
```

Hard rules, because this block is prompt bytes on every run:

- **Outputs come before instructions.** A run that has read its deliverables and
  nothing else can still do the right work; a run that has read twenty
  instructions and no deliverable cannot. Truncation therefore eats the brief and
  the directives, never the outputs list.
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
   ├─ record deliveries: every output the run produced gets a
   │  project_output_deliveries row pointing at the card, file or message that
   │  is the artefact (§6.1)
   └─ close: when every step card is done|archived, or a checkpoint waits, or
      the budget/ask stops it → status, outcome, summary, then the retro +
      score_self (§8)
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

### 6.1 A run closes against its outputs, not against its cards

Closing a run asks one question the board cannot answer: **did the deliverables
arrive?** So on close the run writes a `project_output_deliveries` row for each
output it produced, and its `outcome` is derived from the required ones:

| required outputs of this run | run outcome |
|---|---|
| all delivered | `delivered` |
| some delivered | `partial` — and the missing titles are named in `outcome` |
| none delivered, cards done | `no_output` — the outcome a review must see |

`no_output` exists because it is the most expensive failure this feature can
have: every card green, twenty minutes of tokens spent, and nothing produced. A
board can only tell you the cards finished.

**Only a human accepts an output.** A run may set an output `delivered`; moving
it to `accepted` is a member action (`POST /{slug}/outputs/{id}/accept`), which
is also what closes a `one_off` project (§9.1). The agent produces; the human
accepts. Same posture as playbook activation (§7.2) and skill promotion (§8.2):
the crossing is what needs a person.

For a `recurring` output on a repeatable project, the output row stays `pending`
forever and its *deliveries* accumulate — one per run. "Delivered last Monday,
missed the Monday before" is then a query, not an archaeology exercise.

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
card-only runs) writes `project_runs.retro`: what was done, which outputs it
delivered, what deviated from the plan, what blocked, what it cost, its own
`score_self` (1–5 against `score_rubric`), and **at most three concrete
proposals**.
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
count, cost, outcome, **outputs delivered** and **score**, and the project page
renders them as one small table.
This is the cheapest possible "learnt from over time" and the one a user will
actually look at: *run 14 took twice as long and cost twice as much as run 13.*

Do not build charts in v1. A table of the last ten runs answers the question.

---

### 8.4 Score [7] — the cheapest learning signal there is

`score_user` is one tap on a run: **1–5, optional, human-only**, with an optional
one-line note. `score_self` is the run's own claim, written unprompted with the
retro. The project's score is **derived** — the mean of the last five
`score_user` values — and shown as "4.2 (last 5 runs)", never as an all-time
average, because an all-time average is unmoved by exactly the thing you want to
see: that it got better.

`projects.score_rubric` is free text ("5 = I sent it without editing"). It is
optional, but when set it is given to the run alongside the request for
`score_self`, which is what makes self-scores comparable to human ones at all.

Two uses, and no others in v1:

1. **A `score_user ≤ 2` run raises the retro to the top of the project page** and
   pre-fills the "add feedback" field, because a bad score with no stated reason
   teaches nothing (§5).
2. **`score_self` − `score_user` is reported on the runs table** when they differ
   by ≥2. A run that thought it did well and did not is the highest-value thing
   in this record: it is the case where the *method* is wrong rather than the
   execution, and it is the natural input to a playbook revision proposal (§8.2).

No score-driven automation. Nothing decides to re-run, escalate or change
autonomy based on a score — that is a human's call and a low-scored run is
exactly the situation where an automatic reaction is least welcome.

---

## 9. Progress [5] and health

### 9.1 Progress is derived, and the ladder is ordered

Progress is mandatory to show (field [5]) and impossible to store honestly, so
the API computes one `progress` object on read using the **first rung that
applies**:

| rung | when | headline |
|---|---|---|
| 1 | required outputs exist and any are `accepted`/`delivered` | **outputs accepted / required** — "2 of 3 outputs accepted" |
| 2 | no delivery yet, but a primary goal is linked | the goal's metric progress (FG-29 rollup) |
| 3 | neither | the card rollup as a *ratio*, labelled as such — "8 of 19 cards done" |
| 4 | a `standing` project | never a percentage — "reviewed 21d ago", plus this period's deliveries |

Rung 1 outranks rung 3 deliberately. Cards-done is the number that is always
available and always slightly dishonest: it counts the work, not the result, and
a project can be 18/19 cards with nothing delivered. Whatever rung is used, the
UI states which one — the label is part of the number.

The card rollup (`3 running · 1 blocked · 8/19 done`) is shown *beside* the
headline at every rung, because it is what tells you whether progress is
currently moving.

For a repeatable project, "progress" of the project as a whole is meaningless;
the page shows **this run's** progress plus the delivery streak of its recurring
outputs ("delivered 11 of the last 12 Mondays"). Do not render a lifetime
percentage for something that never ends.

### 9.2 Health

One derived `health` value per project, computed on read, never stored:

| health | when |
|---|---|
| `ok` | nothing below applies |
| `attention` | a card is `blocked`; a run is `waiting` on an ask or budget; a `standing` project is past `review_every`; a `one_off` project is past `due_at`; a run closed `no_output` or `partial` (§6.1); the last run scored ≤2 |
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

**Goals.** `project_links(kind='goal')` is the join to FG-29, and a linked goal
is **rung 2** of the progress ladder (§9.1): it is the headline when the project
has declared outputs but delivered none of them yet. A project with no linked
goal shows the outputs count, or — failing that — a card ratio *explicitly
labelled as one*; never a bare `0%`, and never a card ratio quietly standing in
for an outcome. Field [1] (`projects.goal`, the sentence) is mandatory; a linked
goal *object* in the FG-29 tree is not, because most projects serve a goal nobody
has modelled yet — and the sentence is what a run is told either way.

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
   Additionally: any member may create a project, add *links*, add a **contact**,
   propose an **output** and **accept** one, and **score** a run — those are
   judgements about the work, which is what membership is for. Only `lead`/`admin`
   may add a **profile**, activate a **playbook**, change **autonomy**, set
   **tools/skills**, or set a **schedule** — the five acts that spend tokens or
   change what the system will do unattended.
   A `viewer` additionally never sees `project_contacts.address` (§2.2): the field
   is dropped from the response, not blanked, so a client cannot leak it back.
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
| `POST /` | create: **goal [1], description [2], at least one output [3], host profile + creator as lead [4]** — refused without them (§2.2) — plus `name` (defaults from `goal`, §2.2), slug?, icon/colour, `cadence`, `target_audience`, `definition_of_done`, `board_slug` (bind or create), folders, first goal link |
| `GET /{slug}` | the whole record in one read: fields 1–15 — outputs + their deliveries, members + profiles, contacts (members only), active playbook, active directives, tools/skills (with the host-profile intersection applied so the UI shows what would *actually* run), derived progress + score + health, board rollup, links grouped by kind, last N `task_events`, last 5 runs |
| `POST /{slug}/archive` | shelve it (§13): `archived=1` **and** `status='archived'` in one transaction, plus `detach_project_schedule()` so a shelved project cannot keep firing. Optional `reason`, recorded with who did it. Lead / instance admin. Returns the **updated project row**, never an ack |
| `POST /{slug}/restore` | bring it back: `archived=0`, `status='paused'`. Deliberately not straight to `active` — re-entry is a decision, and `paused` keeps the §2.2 exit gate meaningful. The schedule is **not** re-created; `PUT /schedule` does that |
| `DELETE /{slug}?confirm=<slug>` | hard delete, the narrow case (§13): **human-only** (`_require_human`), owner or lead, and refused `409` unless the project is already archived **and** carries no run, no delivered or accepted output, and no card. Anything else has history: archive it. Clears the `project_meta` active pointer, detaches the schedule, then deletes — the `project_*` tables cascade on the FK |
| `PATCH /{slug}` | `goal`, `name` (independently — re-wording the goal never silently renames the project, and renaming never rewrites the goal), description, status, cadence, `due_at`, `review_every`, `target_audience`, `score_rubric`, icon/colour, visibility, `board_slug`, `definition_of_done`, `max_in_progress`, `budget_usd_per_run` |
| `PATCH /{slug}/tools` | `toolsets` [13] + `skills` [14]; validates names and returns the resolved intersection, so an impossible request is refused loudly (§4.1) |
| `GET /{slug}/outputs`, `POST /{slug}/outputs`, `PATCH /{slug}/outputs/{id}`, `DELETE /{slug}/outputs/{id}` | the deliverables [3]; delete refuses on the last required output |
| `POST /{slug}/outputs/{id}/deliver` | record a delivery (run, card, or a hand-attached artefact) |
| `POST /{slug}/outputs/{id}/accept` | **human-only** acceptance (§6.1); accepting the last required output offers to close a `one_off` project |
| `GET /{slug}/contacts`, `POST /{slug}/contacts`, `PATCH /{slug}/contacts/{id}`, `DELETE /{slug}/contacts/{id}` | contacts [10]; `address` is omitted from every response to a `viewer` |
| `POST /{slug}/runs/{n}/score` | `score_user` + `score_note` [7]; human-only, editable, never agent-writable |
| `PATCH /{slug}/autonomy` | `autonomy` + `require_approval` (separate route so the audit line and the permission check are unmistakable) |
| `PUT /{slug}/schedule`, `DELETE /{slug}/schedule` | create/update/pause the host profile's cron job; refuses without an active playbook |
| `POST /{slug}/members`, `DELETE /{slug}/members/{user_id}` | membership + role |
| `POST /{slug}/profiles`, `DELETE /{slug}/profiles/{profile}` | which instruments the project runs on |
| `POST /{slug}/links`, `DELETE /{slug}/links` | attach/detach a file [11], arrival, to-do, goal, memory document [12], conversation [15], sample or reference [8], or plain URL — `kind` is validated against the known set |
| `GET /{slug}/playbook`, `POST /{slug}/playbook`, `POST /{slug}/playbook/{rev}/activate` | the method and its revisions (cycle-checked on save) |
| `GET /{slug}/directives`, `POST /{slug}/directives`, `POST /{slug}/directives/{id}/retire` | guidance and feedback |
| `GET /{slug}/runs`, `GET /{slug}/runs/{n}` | the record; the list carries duration, cost, outcome, outputs delivered and both scores (§8.3); detail adds cards, the directives the run actually carried, cost from C8, deliveries and the retro |
| `POST /{slug}/runs` | start a run now (`trigger='manual'`, optional `playbook_rev` to repeat an old method) |
| `POST /{slug}/runs/{n}/continue` | pass a checkpoint / answer a budget stop |
| `POST /{slug}/runs/{n}/cancel` | stop promoting; archive this run's un-started cards; never kills a running worker |
| `POST /{slug}/runs/{n}/stop` | stop the run now: reclaim every running card (which terminates its worker) and block it so the dispatcher cannot respawn it, archive what has not started, close the run `cancelled`. The verb `cancel` deliberately is not this one |
| `POST /{slug}/runs/{n}/retro` | write or edit the retrospective |
| `GET /{slug}/board` | the project's columns — `list_tasks(project_id=…, principal=…)` plus the same rollups the dashboard computes, **through the shared helper** |
| `POST /{slug}/cards` | create a card carrying `project_id` (optionally `from_todo`, §10) |
| `PATCH /{slug}/cards/{task_id}`, `GET /{slug}/cards/{task_id}`, `POST /{slug}/cards/{task_id}/comments` | the shipped card behaviours, through `kanban_db` transitions (which still refuse a direct `running`) |
| `GET /{slug}/activity` | merged tail: `task_events` for the project's cards + C8 traces for its runs and linked sessions |
| `GET /{slug}/conversations` | [15] sessions whose cards carry `project_id` (`tasks.session_id`) + run sessions + explicitly linked ones, merged and de-duplicated — automatic first, so linking a conversation is the exception |
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
▣  Acme rollout             one-off · due Fri   2 of 3 outputs accepted   ok
   Acme is live on prod with the team trained            ← [1], one line, dimmed
   4 members · 2 profiles · 3 running, 1 blocked · goal: Land Q3 revenue
   ───────────────────────────────────────────────────────────────────────
↻  Send the Monday digest   repeatable · next Mon 09:00 · run 14  supervised
   last run 6d ago · 20 min · $0.42 · 4.2/5 · delivered 11 of last 12 · ok
   ───────────────────────────────────────────────────────────────────────
∞  Keep the inbox clean     standing · reviewed 21d ago          attention
```

Chips: Active · Repeatable · Standing · Attention · Paused · Done · All. The
cadence glyph (`▣` one-off, `↻` repeatable, `∞` standing) is the one piece of
information that changes what a user expects from a row, so it is the first
thing on it. The bold line is `name`; the dimmed line under it is `goal` [1],
truncated to one line — the reason the two are separate fields is visible here:
one has to fit a row, the other has to say what success means.

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
[Brief] [Outputs] [Progress] [Board] [Runs] [Plan] [Guidance] [People] [Files]
[References] [Memories] [Tools] [Conversations]

Brief   [1][2][6]  the goal sentence — shown *under* the project's label in the
              header, since they are two fields — then the requirements as markdown
              (collapsed past ~12 lines), the target audience as a chip. First
              panel, because it is the only one that says what this project is;
              editable in place by a lead.
Outputs [3]   the deliverables: spec, required/optional, status, and each
              delivery ("delivered run 14 → digest.md"). Undelivered required
              ones first. "Accept" is a button here and nowhere else (§6.1).
              A recurring output gets a 12-cell streak strip.
Progress [5]  the ladder's headline WITH its label (§9.1) and the card rollup
              beside it; then blocked cards — a blocked card is the only thing
              on this page asking for a human right now.
Board         the project's cards; one column per screen on a phone with ‹ ›.
              Each card: assignee profile, run state, comments, child N/M,
              diagnostics badge. Tap → /projects/[slug]/cards/[id].
Runs [7]      the last ten runs: no, trigger, when, duration, cost, outputs
              delivered, score (user, and self when they differ by ≥2),
              outcome, retro present?; tap → the run page (its cards, its
              trace, its deliveries, its retro, score-it, "repeat this run").
Plan [9]      the active playbook: the prose plan + the step DAG (an indented
              list, not a graph widget), its revision and who activated it;
              proposed revisions awaiting activation, with a diff against the
              active one. Labelled "Plan" — "playbook" is our word, not the
              user's.
Guidance      standing instructions newest-first with author and date, an
              "Add instruction" field that says "applies from the next run",
              retired instructions behind a disclosure, and the open ask when
              a run is waiting.
People [4][10] participants — members (avatar, role) and profiles (name, what
              it is running now, which is the host) in one list, because "who
              is on this" means both — then contacts below a rule, marked
              external, with `address` hidden from a viewer.
Files [11]    linked /files assets + every task_attachments blob on the
              project's cards, one grid. A file that arrived on WhatsApp and a
              file a worker attached are the same thing to whoever is looking.
References [8] samples ("match this") kept separate from references ("read
              this"), plus linked arrivals (quoted, → /inbox/<id>), linked
              to-dos with their stages, and plain URLs; each row shows its
              profile.
Memories [12] linked memory documents with their profile and a preview;
              unresolvable ones greyed with "you don't have access".
Tools [13][14] the toolsets and skills a run starts with, showing the resolved
              intersection with the host profile and, explicitly, anything the
              project asked for that the profile does not allow (§4.1).
Conversations [15] run sessions + sessions that produced the project's cards +
              linked ones: title, when, message count, → /chat/<id>, plus the
              C8 trace tail.
```

Panels for empty optional fields collapse to a single "Add …" affordance rather
than disappearing — a missing panel is indistinguishable from a feature that does
not exist — except References and Memories, which hide entirely when empty
because they are the two most often genuinely irrelevant.

**Creation is a two-step form, not a wizard**: step 1 is [1] (the goal; the label is
prefilled from it and editable inline, so the split costs the user nothing), [2] and one [3]
output (everything mandatory except participants, which default to you + the
current profile); step 2 is "how should this run" — cadence, autonomy, host
profile — and is skippable into a `manual` `one_off`. Ten optional fields on a
create form would guarantee nobody fills in the four that matter. The form pins
the host profile to the profile serving the page (a read-only field) — the
record lives where you can see it, so hosting a project on a *named* profile
stays a CLI act, not a form choice.

**The create form needs a door.** The list header carries a primary **New project**
action, and the empty state's CTA is the same one — a Projects page with no way to
create a project is not a missing nicety, it is the feature being unreachable for
everyone except a CLI operator. `/projects/new` is a server-rendered route (not a
modal): the two mandatory Markdown-ish fields need room, a refused submit must be
linkable and reloadable without losing what was typed, and the BFF's 422 `missing`
list maps onto the field that is blank. On success it redirects to
`/projects/[slug]`, where the new project is already readable.

### Removing a project — archive is the verb, delete is the exception

A project is a durable record, so "remove" cannot mean "forget". Two verbs, and
the UI must not present them as siblings:

- **Archive** is the ordinary one: reversible, keeps every run, output, retro and
  score, drops the project out of the list, and stops it running. It is the
  answer for "I'm done with this" and for "this was a mistake, three runs ago".
- **Delete** exists for the genuinely empty mistake — the project created with a
  typo in its goal that never ran. It is refused the moment there is anything to
  learn from: any run, any delivered or accepted output, any card.

Why delete is narrow rather than a big red button with a cascade: the record's
child tables cascade on the foreign key, but **cards do not** — `tasks.project_id`
lives in the per-profile kanban store, a different database with no FK back to
`projects`. A hard delete of a project with cards would leave the board pointing
at a project id that no longer resolves, and the board is somebody's actual work.
Refusing is honest; a cascade across two stores is a data-loss feature.

The affordances, and where they are *not*:

```
/projects/[slug]  ⋯ overflow menu, never a bare button in the header
                  ├─ Archive project…        → reason field, one confirm
                  ├─ Restore project         (only when archived)
                  └─ Delete permanently…     (only when the §12 preconditions
                                              hold; typed-slug confirmation,
                                              and the dialog says what would be
                                              lost and what archive keeps)
/projects         Archived chip beside All — restore is only discoverable if a
                  shelved project can be found again
```

Every one of these is a `BusyRegion` write that merges the **returned project
row** (the ed.3.1 lesson: a write that returns an ack the UI renders as a row is
indistinguishable from a write that did nothing), and delete redirects to
`/projects` with the row gone rather than leaving a detail page for something
that no longer exists. A `viewer` sees none of the three; a member who is not a
lead sees Archive disabled with the reason, not hidden — "why can't I" is a
better question than "where did it go".

**A shelved project does not run and does not learn.** Archive stops
*execution and learning* — while archived, every act that would grow what
the project does or knows — start, continue, retro or score a run, add a
card, save or activate a playbook, declare, change or deliver an output,
accept an output, add or activate guidance, set a schedule, change the
tools or the autonomy, re-summarise — is refused `409` naming the archive
and pointing at restore; the panels and the run page hide the same
affordances with the same hint. The gate also holds one layer below the
router: the card writer itself (`kanban_db.create_task`) refuses an
archived project, so the to-do promote route and
`hermes kanban create --project` cannot grow the board either. Record
bookkeeping deliberately stays open — it writes the *record*, not what
the project does: correcting a typo (`PATCH /{slug}`), links (how samples,
references, files, memories and conversation histories attach to a
project), member/profile/contact bookkeeping, every DELETE verb and
directive retirement, and cancel. Archive itself refuses to shelve a
project holding a `running`/`waiting` run — it asks the store for *every*
open run, not a page of the newest — so cancel is the sanctioned way out
of an open run. Restore is the one act that unblocks everything else.

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
src/app/projects/new/page.tsx                  the two-step create form (§13)
src/components/projects/NewProjectForm.tsx     client form, 422 → field errors
src/components/projects/ProjectLifecycleMenu.tsx  archive / restore / delete…
src/app/projects/[slug]/page.tsx               detail
src/app/projects/[slug]/loading.tsx
src/app/projects/[slug]/runs/[no]/page.tsx     one run
src/app/projects/[slug]/cards/[id]/page.tsx    one card
src/app/api/projects/**                        BFF mirror, one route per endpoint
src/components/projects/ProjectsList.tsx       + ProjectCard, ProjectsFilters
src/components/projects/ProjectDetailView.tsx  orchestrates the panels
src/components/projects/panels/{Brief,Outputs,Progress,Board,Runs,Plan,Guidance,People,Files,References,Memories,Tools,Conversations}Panel.tsx
src/components/projects/OutputsEditor.tsx      + OutputDeliveries, AcceptButton
src/components/projects/ScoreControl.tsx       1–5 + note, on the run page
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
`projectOutputs()`, `addProjectOutput()`, `updateProjectOutput()`,
`deliverProjectOutput()`, `acceptProjectOutput()`, `scoreProjectRun()`,
`projectContacts()`, `addProjectContact()`, `setProjectTools()`,
`projectMembers()`, `addProjectMember()`, `addProjectProfile()`,
`archiveProject()`, `restoreProject()`, `deleteProject()`,
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
hermes projects create    "<goal sentence>" [--name "<short label>"]
                                 --description <file.md|-> --output "<title>"
                                 [--cadence …] [--goal <id>] [--host-profile p]
                                 [--audience "…"]          # 1,2,3,4,6 — first three required
hermes projects link      <slug> --kind file|arrival|todo|goal|memory|session|url|
                                        sample|reference
                                 --profile <p> --ref <id>
hermes projects outputs   <slug> [list|add "<title>" [--spec s] [--optional]
                                 |deliver <id> --ref <r> |accept <id>]
hermes projects contacts  <slug> [list|add "<name>" [--role r] [--platform p]
                                 [--address a]]
hermes projects tools     <slug> [show|set --toolsets a,b --skills x,y]
hermes projects score     <slug> <run_no> <1-5> [--note "…"]
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

`hermes projects create` refuses without fields [1]–[3], and `--description -`
reads stdin, because a mandatory long brief typed as a shell argument is a brief
nobody writes.

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
3b. **Declare the output before doing the work, and never accept your own.**
   `outputs add` is proposable by the agent; `outputs accept` is human-only, and
   a run that produced nothing must close `no_output` rather than narrate
   effort (§6.1).
4. **Never move a card past a checkpoint, never activate a playbook, never widen
   autonomy.** Those are human acts by design.
5. **Read the last run's retro and score before starting work** — that is what
   the record is for, and a `≤2` score with a note is the most specific
   instruction the project has.
6. **A project's `toolsets`/`skills` can only narrow what the profile allows.**
   If a run needs a tool the profile does not enable, that is an ask (§5.3), not
   a project edit.

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
9. **Effort mistaken for delivery.** Every card done, nothing produced. →
   mandatory declared outputs, `outcome='no_output'`, progress rung 1 outranking
   the card ratio, and health `attention` on a `no_output`/`partial` close.
10. **Optional fields that are not really optional.** A page that breaks, or a
   run prompt reading "Audience: None", when fields 6–15 are empty. → headings
   omitted with their field, collapse-to-"Add …" panels, and an explicit
   all-optionals-empty test.
11. **`toolsets` read as a grant.** A project handing its runs a capability the
   host profile withholds. → intersection only, drops recorded, unknown names
   refused (§4.1).
12. **Retro theatre.** A retro that summarises intentions instead of artefacts
   teaches nothing. → written from cards, `task_runs`, events and the trace, and
   capped at three concrete proposals.

---

## 16. Testing

Behaviour contracts, not change detectors (`AGENTS.md`).

> **Review of the shipped steps 1–8 lives in
> [`docs/reviews/2026-08-13-projects-steps-1-8-review.md`](../../../reviews/2026-08-13-projects-steps-1-8-review.md)**
> — 9 backend findings (H1–H4, M1–M3, L1–L2) and 8 agent-home findings
> (F1–F8), each with the call site, the shipped seam to use and the test that
> would have caught it. Read it before continuing at step 9.

**Fields (the §1.1 contract)**
- Create is refused without goal [1], description [2], ≥1 output [3], or a host
  profile [4]; each rejection names the missing field.
- `name` is derived from `goal` when omitted, is ≤60 chars, and is **not** rewritten
  by a later `PATCH` of `goal`; patching `name` alone leaves `goal` untouched.
- A project with fields 6–15 all empty renders every page and compiles a run
  prompt containing **no empty headings** and no literal "None" (the optionality
  regression that would be least visible).
- `goal` >160 chars, `name` >60 chars and an empty `goal`/`description` are refused
  at the store, not only the router (the CLI path).
- Deleting the last required output is refused; deleting an optional one is not.
- Progress uses rung 1 when a delivery exists even if cards are 0/19, and falls
  to rung 3 with the label "cards" when no goal and no delivery exist (§9.1).
- `score_user` is human-only: an agent-authenticated principal is refused; the
  project's score is the mean of the **last five** and moves when an old run is
  re-scored.
- `toolsets` narrowing only: a project naming a toolset the host profile does not
  enable spawns **without** it, records the drop on the run, and never grants it;
  an unknown toolset/skill name is rejected at write time.
- `viewer` responses omit `contacts[].address`; members' do not.
- Accepting the last required output on a `one_off` project offers closure and
  does **not** close it automatically.

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
  renders every panel from one fetch; empty optional panels collapse to "Add …"
  (and References/Memories hide) rather than rendering empty;
  the two-step create form blocks on [1]–[3] and defaults [4]; `filters.ts` passes the server/client
  boundary test; nav tests updated for the secondary slot and the Home card.
- **The create action exists on the page**, not only in the API: the list header
  and the empty state both route to `/projects/new`, and the form's submit posts
  the §2.2 four. A test that asserts `createProject()` exists on the client while
  no rendered surface calls it is exactly the hole that shipped.
- A 422 from create names the blank field in the form rather than a toast, and a
  refused submit preserves what was typed.

**Lifecycle (§13)**
- Archive sets `archived=1` **and** `status='archived'`, and a project with a
  schedule has its cron job detached by the same call — the invariant is that no
  archived project has a live `cron_job_id`.
- An archived project is absent from the default list, present under Archived,
  and its detail page still renders the whole record read-only-ish (restore is
  the only write offered).
- Restore lands in `paused`, never `active`, and does **not** resurrect the
  schedule.
- While archived, every growing act — start/continue/retro/score a run, add
  a card, save or activate a playbook, declare/change/deliver/accept an
  output, add or activate guidance, set a schedule, change the tools or the
  autonomy, re-summarise — is refused `409` until restore, and the refusal
  also holds at the card writer (`kanban_db.create_task`), so the to-do
  promote route and `hermes kanban create --project` inherit it. What stays
  allowed is record bookkeeping, not growth: `PATCH /{slug}` (record
  fields), links, member/profile/contact bookkeeping, the DELETE verbs,
  directive retirement, and cancel. Archive refuses to shelve a project
  holding a `running`/`waiting` run — it scans every run, not a page — and
  cancel (or finishing it) is the way out.
- Delete refuses (409, naming what it found) when the project has a run, a
  delivered or accepted output, a card, or is not archived; it refuses (403)
  for a session-less or agent caller, and for a member who is not a lead; and
  the `confirm` parameter must equal the slug.
- A permitted delete leaves nothing behind: the `project_*` rows are gone, the
  active-project pointer no longer names it, no cron job survives, and
  `GET /{slug}` is a 404 for everyone.
- Archive and delete both return the row shape the UI merges, and the UI reflects
  the new state without a reload.

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
| 1 | **Store**: root-anchored `projects_db` + import migration + `project_members`, `project_outputs`, `project_output_deliveries`, `project_contacts`, `project_playbook`, `project_directives`, `project_runs`, `project_run_cards` + the additive columns (incl. `target_audience`, `score_rubric`, `toolsets`, `skills`) + the §2.2 constraints; `list_tasks(project_id=…)` + its index; **repoint the Electron app and `hermes project`** | Nothing else uses it yet. The repoint rides along (ed.2 Q5) because two live stores diverge daily. |
| 2 | **`kanban_view.py`**: extract `plugin_api.get_board`'s aggregation, repoint the dashboard | Pure refactor, no behaviour change, landed alone so a regression is unambiguous. |
| 3 | **API part 1**: `projects_api.py` — record, **outputs + deliveries + accept**, members, profiles, **contacts**, links (incl. `sample`/`reference`/`memory`), **derived progress (§9.1)**, board, cards, the permission matrix, the fan-out | Backend only. All five mandatory fields are readable and writable at the end of this step. |
| 4 | **Runs + playbook + guidance**: the run lifecycle, playbook instantiation via parent links, the compiled prompt block (§5.2, incl. outputs/audience/samples), the `toolsets`/`skills` intersection at spawn (§4.1), checkpoints, caps, budget, delivery recording + `no_output` (§6.1) | Depends on `agent/seeded_session.spawn_seeded_session()` (To-dos ed.2 step 8) for `mode:'inline'`; card-only runs do not. |
| 5 | **Schedule**: `PUT/DELETE /schedule` wiring the host profile's cron job, `next_run_at`, `health`, `doctor` | |
| 6 | **BFF + client + types** in `agent-home` | |
| 7 | **List page** + Home card + nav slot | |
| 8 | **Detail page**: all nine panels, the card route, the run route, `AddToProjectSheet` + "Add to project" on `/todos`, `/inbox`, `/files` | |
| 8b | **Promotion**: `from_todo` on `POST /{slug}/cards`, the `/todos/[id]` promote action, the link-vs-promote copy | A promote button with no project page to land on is a trapdoor, so it follows 8. |
| 9 | **CLI + skill**: `hermes projects …`, `skills/productivity/projects/SKILL.md` | The agent's route in. |
| 9b | **Score**: `POST /runs/{n}/score`, `score_self` with the retro, the derived project score, the ≥2 divergence column, `score_rubric` | Needs runs to exist; cheap and independently useful. |
| 10 | **Retro + learning**: retro write-back, proposed playbook revisions and directives, skill-candidate provenance into `background_review` | Last, because it needs real runs to be worth anything. |
| 11 | **Events + summary**: `GET /{slug}/events?since=`, `POST /{slug}/summarise` | |
| 12 | **Lifecycle API**: `POST /{slug}/archive`, `POST /{slug}/restore`, `DELETE /{slug}` with the §13 preconditions + the human gate, and the CLI verbs `projects archive/restore/delete` (delete behind `--as-human --confirm <slug>`) | Backend first, so step 12b's menu cannot ship faster than the rules it depends on. The store functions (`archive_project`, `restore_project`, `delete_project`) already exist from the folder-workspace era — this is the router, the preconditions and the cleanup, not new SQL. |
| 12b | **Management UI**: `/projects/new` + `NewProjectForm`, the list header and empty-state action, `ProjectLifecycleMenu` (archive / restore / delete…) and the Archived chip | The step that closes the gap the owner hit: create and remove existed at every layer *except* the one a user touches. |

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
- **ed.3.2 (this edition) resolves two field questions with the owner:**
  Samples/References are **optional**, and **Goal is its own field** — a new
  `projects.goal` (the mandatory outcome sentence) beside the existing
  `projects.name` (the short label), which now defaults from it at create.
- **ed.3.1 adds, from the owner's field list:** mandatory outputs
  (`project_outputs` + `project_output_deliveries`, and `no_output` as a run
  outcome), contacts (`project_contacts`), target audience, score
  (`score_user`/`score_self`/`score_rubric`), project-level tools and skills as a
  narrowing filter, `sample`/`reference`/`memory` link kinds, the ordered progress
  ladder (§9.1), and the panel set + create form that make ten optional fields
  survivable. It also **renames** the Method panel to **Plan** and drops the
  Resources panel in favour of References + Memories.
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
11. **The goal and the name are two fields** (owner decision, 2026-08-14): `goal`
    is the mandatory outcome sentence, `name` is the short label every list, board
    and slug uses. `name` defaults from `goal` at create and never tracks it after.
12. **Outputs are mandatory and declared up front**, and a run is judged against
    them (`delivered | partial | no_output`) rather than against its cards. Only
    a human accepts an output.
13. **Progress is an ordered ladder — outputs, then goal metric, then cards —
    labelled with the rung it used.** The cards ratio is the last resort, never
    silently the headline.
14. **A project's tools and skills narrow, never widen, the host profile's**, and
    are applied at spawn.
15. **Score is 1–5, human-written, with the run's self-score kept beside it.** No
    automation reacts to a score in v1; the divergence is the signal.
16. **Contacts are project-scoped rows, not an address book**, and their
    addresses are member-only PII.
17. **Removal is archive by default; hard delete is the narrow exception**
    (owner decision, 2026-08-18). Archive is reversible, keeps the record and
    detaches the schedule; delete is human-only, requires the project to be
    already archived and to carry no run, accepted/delivered output or card, and
    refuses rather than cascading across the kanban store — `tasks.project_id`
    has no foreign key back to `projects`, so "delete everything" would mean
    silently orphaning somebody's board.

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
| 8 | ~~Samples / References carried no M/O marker.~~ **Answered 2026-08-14: optional.** | Kept split in two — a *sample* ("match this") is named in the run prompt, a *reference* ("read this") is listed for the run to open. Both optional, both pointers. **Closed.** | — |
| 9 | ~~Is “Goal” the project's name, or a separate field?~~ **Answered 2026-08-14: separate.** | `projects.goal` (≤160, the outcome sentence, mandatory) is now distinct from `projects.name` (≤60, the label). The one risk this creates is the two drifting apart — handled by defaulting `name` from `goal` at create, editing them independently, and showing both in the list row and the page header so a stale label is visible rather than hidden. **Closed.** | — |
| 10 | **Score: whose, and on what?** I made it per-*run*, 1–5, human-written, with the run's own `score_self` beside it, and the project score derived from the last five. | **Per run, 1–5, human-only, plus self-score.** Scoring a *project* directly gives you a number nobody updates; scoring runs gives you a trend for free. | Adding a project-level manual score later is additive. |
| 11 | **Outputs on a repeatable project.** I made recurring outputs one row with many deliveries ("delivered 11 of the last 12 Mondays") rather than a fresh output row per run. | **One row, many deliveries.** 52 output rows a year is the same unreadability problem as 52 cards named "Draft the summary". | Cheap: it is a flag plus which table the delivery lands in. |
| 12 | **Should `progress` ever be manual?** A percentage the owner types is honest about judgement and dishonest about staleness. | **No manual override in v1** — the ladder plus the rolling `summary` covers "where this really stands" in words, which is what a human actually reads. | Additive: a nullable `progress_override` column. |
| 13 | **Memories [12]: curated pointers, or a project memory namespace?** I made them pointers into a profile's memory documents. | **Pointers.** A project-owned memory store would be a second memory tier competing with the shipped one and FG-29's promotion path. | A namespace later would be a real design; say now if you want it. |
| 14 | **§1.1 is now the owner's list verbatim, with fields 8 and 9 settled (ed.3.2).** Nothing in it is my reconstruction any more. | **Treat §1.1 as frozen for step 1** — the store PR is the cheapest place to add a column and the most expensive place to have missed a concept. | A column is cheap; a *concept* added after the API and the panels exist is not. |

---

## 20. Implementation status and open defects

The design above is frozen at ed.3.2. This section is the **record of what was
built against it** and what is still wrong; it is maintained by review, not by
design changes. Anything that requires the design to change gets a new edition
above, not a note here.

### 20.1 What landed

All eleven steps of §17 are merged into `develop`, plus two additions the
sequencing did not name (8b, 9b) and a live-integration merge:

| Step | Scope | PR / commit |
|---|---|---|
| 1 | Root-anchored Projects store, migration, profile import | #251 |
| 2 | Kanban board rollup extraction | #252 |
| 3 | Projects API (list/detail/writes, permission gates) | #254 |
| 4 | Run lifecycle, playbooks, guidance, toolset/skill narrowing | #258 |
| 5 | Schedule wiring, health, `doctor` | #259 |
| 6 | BFF routes, client, types | #261 |
| 7 | Projects list page | #263 |
| 8 | Detail page, run and card routes | #266 |
| 8b | `from_todo` promotion seam | `ffb139319` (#279/#280) |
| 9 | `hermes projects` CLI + `skills/productivity/projects/SKILL.md` | #271 |
| 9b | Score routes and the `score` verb | #274 |
| 10 | Retro → learning (playbook rev / directive / skill candidate, all inactive) | #275, #276 |
| 11 | Event tail, rolling `summarise`, phase closeout | #276, #278 |
| — | Live integration into `develop` | #279, #280 |
| 12 | Lifecycle API: archive / restore / delete router, §13 preconditions, human gate, CLI verbs | #305 |
| 12b | Management UI: `/projects/new` + `NewProjectForm`, the `[⋯]` menu, the Archived chip | #305 |
| 12c | Lifecycle hardening (review U2–U6): the archived-inert gate, structured 422 forwarding to the form, the archived-inclusive card count, the flag-aware Archived chip, and the interaction + route tests | #307 |
| 12d | Lifecycle completion (review U7–U8): the gate on every growing route, the archive-time open-run precondition, the run page's archived wiring, and the principal-blind delete count | #310 |
| 12e | Below-router completion (review U9–U12): the writer-level archived gate in `create_task` (promote → 409, no orphan link), the unpaged open-run scan, the U8 consumer and run-page loader tests, and §13's narrowed wording | #312 |
| 12f | Writer-gate residuals (review U13–U15): `ArchivedProjectError` identity with narrowed promote/CLI handlers, plain-field `archived` access, and real-path boundary tests on both doors | #314 |

Verified at `7c737474f`: 185 Projects Python tests pass, 46 agent-home Projects
tests pass, `tsc --noEmit` clean. The feature has **not** been deployed or
system-tested on a live box.

### 20.2 Open defects

Two reviews, both merged as documents, carry the findings and the fix recipes.
**None of the first review's findings had been fixed at the time of the second.**

- [`docs/reviews/2026-08-13-projects-steps-1-8-review.md`](../../../reviews/2026-08-13-projects-steps-1-8-review.md)
  (#270) — 17 findings on steps 1–8 with per-finding call site, runtime effect,
  the shipped seam to use, and the test that would have caught it.
- [`docs/reviews/2026-08-17-projects-end-to-end-review.md`](../../../reviews/2026-08-17-projects-end-to-end-review.md)
  — re-verification of all 17 against the current tree (16 open, F8 fixed by
  step 8b) plus five new findings on steps 8b–11, each with the call site, the
  runtime effect, the fix against a shipped seam and the test that catches it.
  Its closing section is the **ordered fix checklist** for all 21 open items,
  grouped into five independently shippable blocks — that is the list to work
  from.

The design-relevant ones, in the order the second review recommends fixing them:

1. **E1 — the human-only acts are enforceable in one place out of three.**
   §6.1 (accept), §8.1 (score) and §8.2 ("a human approves every crossing")
   describe three human acts. Only `score` has an identity gate, and
   `projects_cli` patches that gate out for the agent's own route (§14), so the
   learning loop can close with no human in it. This is the one finding that
   weakens a *design guarantee* rather than an implementation: the record's
   trustworthiness rests on those three acts.
2. **F1a/F1b/F1c/F4 — three of the four detail-page writes look like failures.**
   Accept-output, continue-run and add-directive return ack envelopes that the
   panels merge as rows, and `RunView` never revalidates. §13's promise that the
   page shows what actually happened is not kept.
3. **H1–H4 — the run lifecycle's four seams are not in force at spawn time.**
   Approvals (§4.3) are never raised, `budget_usd_per_run` (§4.2) is
   unenforceable because runs carry a synthetic `trace_id` bound to no C8 trace,
   toolset narrowing (§4.1, invariant 14) reads the calling process's config
   instead of the host profile's, and an inline run spawns without
   `profile_home` so it executes outside the profile the run row records (§11).
4. **M1/M2/F2 — health and the list contradict §9.2.** A never-run repeatable
   project is never `stalled`, `stalled` projects are excluded from the
   Attention chip that exists to surface them, and the list filters after the
   page slice so paging *loses* matching rows.
5. **E3 — the §12 event tail has no consumer**, so §13's live board updates do
   not happen; the page refreshes only after its own writes.
6. **U1 — the Projects page has no way to create or remove a project** (owner
   report, 2026-08-18). `POST /api/projects` validates the §2.2 four and
   `createProject()` is on the client, but nothing rendered calls either:
   `ProjectsList` has one button and it is "load more", and there is no
   `/projects/new` route. Removal is worse than missing — it exists at no layer
   above the store: `projects_db` has `archive_project`, `restore_project` and
   `delete_project`, the Python router exposes neither (only `PATCH` with a
   `status`, which sets the string without detaching the schedule), and
   `api/projects/[slug]/route.ts` has `GET` and `PATCH` only. So the feature is
   reachable only from a CLI, which is not the primary UI (`AGENTS.md`).
   §13 now specifies the surfaces and §12 the endpoints; steps 12 and 12b
   build them. Note the trap recorded in decision 17: a naive cascade would
   orphan `tasks.project_id` rows in the per-profile kanban store, which has no
   foreign key back to `projects`.
7. **U2–U6 — Block 4b built the doors; two contracts are still unmet**
   (review of `570be680f`, 2026-08-20). The lifecycle store/router/CLI match
   §12 and decision 17, and `/projects/new` + the `[⋯]` menu exist. What does
   not hold yet: **archive stops the cron job but not the project** — no
   mutating route checks `archived`, so a shelved project still accepts a
   manual run, an accepted output and a new directive, and the detail page
   hides only its three header buttons while the Outputs and Guidance panels
   keep their writes (§13 "stops it running", §16 "restore is the only write
   offered"); and **the 422 → blank-field mapping cannot fire**, because the
   BFF bridge flattens a structured `detail` to a string, so the `missing`
   list §13 promises never reaches `NewProjectForm`. Three smaller ones: the
   new BFF routes and client lifecycle methods have no tests and the new
   component tests are markup-only; `deleteEligible` counts cards without
   archived ones while the server counts them; and the Archived chip matches
   on `status` so a row shelved before Block 4b is unreachable from it.
   **Block 4c** of the end-to-end review is the worklist. **FIXED in Block 4c
   (#307)**: every mutating route refuses an archived project `409` and the
   panels hide the same affordances; the bridge forwards a structured `detail`
   so the 422's `missing` list maps onto the blank field; the three new BFF
   routes and both new components have handler-level tests; the detail payload
   carries an archived-inclusive card count the delete gate reads; and the
   Archived chip matches the `archived` flag as well as the status.

8. **U7–U8 — the archived-inert rule is asserted generally and enforced on five
   routes** (review of Block 4c, `b743715d1`/PR #307, 2026-08-21). U3–U6 are
   closed. But `_refuse_if_archived` guards only run / accept / directives /
   tools / schedule, while the §13 paragraph that lands with #307 promises it of
   *every* growing act, so
   `continue` a held run, `retro`, `score`, add a card, save or activate a
   playbook revision, add or deliver an output, raise `autonomy` and
   `summarise` all still land on a shelved project — and archive has no
   precondition on an in-flight run, so a project holding a `waiting` run can
   be shelved and then resumed, which §13's "stops it running" forbids. The run
   page compounds it: it fetches the run only, so `RunView` cannot know the
   project is archived and still offers Continue / Cancel / retro / score.
   Either gate the rest or narrow this section's wording — they must not
   disagree. U8 (low): the delete gate counts cards *with* the caller's
   principal while the delete route counts without one, so a lead who cannot
   see a colleague's private card is offered a Delete the route refuses.
   **Block 4d** of the end-to-end review is the worklist, and the
   "Block 4d — the implementation plan" section after it is the step-by-step:
   the archive-time precondition on an open run, the twelve routes to gate
   (with the deliberately-open list), the run page wiring, U8's
   principal-blind count, and the tests for each. **FIXED in Block 4d
   (#310)**: archive refuses a project holding a `running`/`waiting` run
   (cancel is the sanctioned way out, and stays open on a shelved project);
   the gate now covers every growing route, with the deliberately-open list
   written into `_refuse_if_archived`'s docstring; the run page fetches the
   project alongside the run and `RunView` hides Continue / Repeat / Save
   retro / the score control behind the panels' hint while Cancel stays; and
   the delete gate reads `card_rollup.total_all_principals`, the
   principal-blind `COUNT(*)` the delete route agrees with.
- **U9 (medium) — the archived gate holds at the Projects router and leaks one
  layer below it.** `tasks.project_id` is also written by
  `POST /api/registry/todos/{id}/promote` and
  `hermes kanban create --project <slug>`, both of which reach
  `kanban_db.create_task`, which resolves the project and never reads
  `archived`. So a shelved project can still gain board cards (and, through
  promote, a `project_links` row) — the orphan class hard delete's card blocker
  exists to prevent, and §13's "a shelved project does not run and does not
  learn" is true of the router and false of the system. The fix belongs in
  `create_task`'s project branch, one gate for both callers, with promote
  mapping the refusal to 409. Three lows land with it: **U10** archive's
  open-run precondition scans only the newest 50 runs
  (`list_project_runs(..., limit=50)`), so a standing project's old held run is
  invisible to it — ask in SQL instead; **U11** nothing tests the U8 fix's
  consumer (`ProjectLifecycleMenu` reading `total_all_principals`) or the run
  page's project fetch and its fetch-failed path; **U12** `POST /{slug}/links`
  stays open as bookkeeping, yet links are how samples, references, files,
  memories and conversation histories attach — so either gate it or narrow §13
  to "stops execution and learning; record bookkeeping stays open" with the
  permitted list named. **Block 4e** of the end-to-end review is the worklist.
  **FIXED in Block 4e (#312)**: `create_task` raises on an archived
  project — promote maps the refusal to 409 and the link row never lands,
  `hermes kanban create` prints it instead of a traceback; `_archive_sync`
  asks SQL for *every* open run (no page window); the U8 consumer
  (`ProjectLifecycleMenu` reading `total_all_principals`) and the run
  page's loader (archived / live / fetch-failed / 404) have tests; and §13
  now says what archive truly stops — execution and learning — with the
  open record-bookkeeping list named. **Refined in Block 4f (#314)**:
  the refusal has its own identity (`kanban_db.ArchivedProjectError`,
  a `ValueError` subclass) so promote maps ONLY it to 409 and ordinary
  `create_task` input errors keep their log line and generic failure; the
  gate reads the declared `archived` field; and both doors have real-path
  tests — a genuinely archived project through the real route and the real
  CLI.
- **U13–U15 — three lows left behind by the writer-level gate** (review of
  Block 4e, at `31351551a`). The gate itself is right and reaches every card
  writer, but: **U13** both handlers catch bare `ValueError`, so ordinary
  `create_task` input errors (`initial_status must be one of …`, bad
  `branch_name`) now answer promote's archive-flavoured `409` and lose their
  log line — the refusal should carry its own
  `ArchivedProjectError(ValueError)`; **U14** the gate reads
  `getattr(project_obj, "archived", 0)` on a declared, coerced field, which
  `AGENTS.md` rules out; **U15** the boundary's route test mocks
  `create_task`, so nothing exercises promote against a genuinely archived
  project and `hermes kanban create --project <archived>` has no test at all.
  **Block 4f** of the end-to-end review is the worklist.

### 20.3 Testing gaps that let the above through

§16's contracts are behaviour-shaped and the store/router honour them. Two
holes account for nearly every open finding, and both should be closed before
the next step lands:

- **The run seams are only ever tested through injected fakes**, so defects in
  the *default* implementations of `spawn_inline`, `cost_reader` and the
  approval hook are invisible by construction. §16 needs one contract per seam
  asserting the real default resolves a real symbol.
- **The ~34 BFF routes have no tests**, and the client tests assert URLs rather
  than response handling — which is exactly the F1 bug class. One test per write
  feeding the actual upstream envelope through the panel's state update closes
  it.
