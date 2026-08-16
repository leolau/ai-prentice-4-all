---
title: "feat: Projects — one place to finish a piece of work with other profiles and people"
status: draft — spec for review
date: 2026-08-12
type: feature
target_repo: ai-prentice-4-all
origin: user request — "review the existing kanban feature inside the dashboard UI and come up with a plan to create a similar but new UI page, called Projects, in the agent-home interface. This new Projects page aims to allow user to focus on the tracking and finishing a project with other profiles / users together. This is similar to Kanban and would like to use the existing kanban infrastructure as much as possible. But from the user interface point of view, I want to be able to see all the related files, resources, users, past conversations, progress and goal all in one place."
depends_on:
  - hermes_cli/kanban_db.py (the shared, cross-profile board — the execution substrate)
  - hermes_cli/projects_db.py (the Project record that already exists, and its `board_slug` binding)
  - plugins/kanban/dashboard/plugin_api.py (the dashboard's board API — the shape to learn from, not to proxy)
  - docs/plans/2026-08-11-001-todos-staging-layer-plan.md (the agent-home surface conventions this follows)
  - docs/design/master-plan/feature-groups/FG-27-profile-scoped-datastore-isolation.md (why cross-profile aggregation is a fan-out, not a join)
  - docs/design/master-plan/feature-groups/FG-28-multi-profile-administration.md (one GoTrue subject namespace; the `principals` row as entitlement)
  - docs/design/master-plan/feature-groups/FG-29-goal-tree-and-insight-promotion.md (the goal a project serves)
---

# Projects

> **Superseded for implementation by** `docs/design/master-plan/feature-groups/FG-32-projects-durable-record.md`
> (ed.3), which consolidates this plan and ed.2 into one standalone spec and adds
> the four dimensions neither covers: cadence (one-off / repeatable / standing),
> autonomy and its caps, guidance (durable directives and feedback), and runs /
> retros / the learning loop. Implement from that document; this plan remains the
> record of the substrate review and the reasoning behind the store decision.
>
> **Amended by** `docs/plans/2026-08-13-001-todos-and-projects-design-revision.md`
> (ed.2): all six open questions in §11 are answered there as defaults (with the
> desktop repoint folded into step 1), and the to-do → card promotion this plan
> mentions in §5 is specified there as step 6b.

## What this is for

To-dos answered *what needs me next*. Projects answers the other half: **what
are we trying to finish, together, and where does everything about it live?**

Today a piece of work that spans more than one sitting is scattered across six
surfaces that do not know about each other. The board that executes it is in
the operator dashboard. The files that arrived for it are in `/files`. The
messages that started it are in `/inbox`. The people are in `/members`. The
conversations are in `/chat` and `/activity`. The goal it serves is in `/graph`.
Nobody can answer "how is the Acme rollout going?" without opening five tabs and
holding the join in their head.

A **Project** is that join, made an object:

```
   goal it serves          the work                the context               the people
   ──────────────          ─────────               ───────────               ──────────
   FG-29 goal tree   →   kanban board        →   files / arrivals   ←→   principals (users)
   (shipped)             (shipped)               conversations           profiles (instruments)
                         to-dos + tasks          runs / traces           (FG-26 / FG-28)
                         (shipped)               (shipped)
```

Every box above already exists and is already tested. **This plan adds no
execution engine and no second board.** It adds a project record that names the
work, a membership model that says who and which profiles are in it, a link
table that gathers the context, and one `agent-home` surface that renders all of
it in one place.

## Review of the existing Kanban feature (what we are reusing)

Read before designing; summarised here because the design decisions below are
consequences of it.

| piece | where | what it gives us |
|---|---|---|
| **Board store** | `hermes_cli/kanban_db.py` (8.8k LOC) | `tasks`, `task_links` (parent/child DAG), `task_comments`, `task_events` (append-only, the change tail), `task_runs` (one row per attempt: claim, PID, heartbeat, outcome, summary), `task_attachments` (blob on disk + metadata), `kanban_notify_subs`. Nine statuses (`triage todo scheduled ready running blocked review done archived`), typed block reasons, an unblock-loop breaker, a consecutive-failure circuit breaker. |
| **Deliberately root-anchored** | `kanban_db.kanban_home()` | The board DB lives at the **shared root**, never under `$HERMES_HOME`, with a comment that says why: "profiles intentionally collapse onto a shared board: it IS the cross-profile coordination primitive." This is the single most important fact for this plan. |
| **Multiple boards** | `<root>/kanban/boards/<slug>/`, `<root>/kanban/current` | Named boards with `name`/`description`/`icon`/`color` metadata, per-board workspaces and attachment roots, create/rename/archive/switch. |
| **Dispatcher + workers** | `gateway/kanban_watchers.py`, `hermes_cli/kanban.py` | The gateway hosts the dispatcher (60 s tick): reclaim stale claims, promote `ready`, spawn a worker per card under the assigned **profile**, heartbeat, terminate on runtime cap. Plus swarm topologies (`kanban_swarm.py`), decomposition (`kanban_decompose.py`), specification (`kanban_specify.py`) and diagnostics (`kanban_diagnostics.py`). |
| **Assignee = profile** | `tasks.assignee` | A card is assigned to a *profile*, not a person. Cross-profile collaboration is already the model; what is missing is the human-facing frame around it. |
| **Dashboard UI** | `plugins/kanban/dashboard/` (manifest + `plugin_api.py`, 2.4k LOC, compiled `dist/`) | 40 routes: `/board` (columns + link/comment counts + child-progress rollup + diagnostics + latest run summary), `/tasks/*` CRUD, attachments upload/download, comments, links, bulk ops, `/workers/active`, `/runs/*` incl. inspect + terminate, `/dispatch`, `/boards*`, `/profiles`, `/orchestration`, and a `/events` WebSocket that tails `task_events`. Auth is the **dashboard's** session-token middleware. |
| **A Project record already exists** | `hermes_cli/projects_db.py` + `hermes_cli/projects_cmd.py` (`hermes project`) | `projects(id, slug, name, description, icon, color, board_slug, primary_path, archived)`, `project_folders`, `discovered_repos`; `tasks.project_id` already exists on the board and already anchors a card's worktree under the project's primary repo with a deterministic branch. **But it is per-profile** (`$HERMES_HOME/projects.db`) and repo-shaped (folders, git roots) — built for desktop session grouping, not for people. |

Three properties of that review drive everything below:

1. **The board is already the cross-profile primitive.** Anything that makes a
   per-profile copy of it is wrong.
2. **The board has no RLS.** It is SQLite. It carries C2's *vocabulary*
   (`owner_user_id`, `visibility`, and `list_tasks(principal=…)` filters
   `shared` + own-`private`), but enforcement is app-layer at one seam. Because
   all profiles now share one GoTrue (FG-27/FG-28 decision, 2026-08-10), a
   `user_id` finally means the same person box-wide — which closes the identity
   ambiguity FG-27 recorded as an open risk and is what makes a *people*-shaped
   Projects page honest at all.
3. **The dashboard's board API is not reachable from `agent-home`.** It is a
   dashboard *plugin* route behind the dashboard's own auth. `agent-home` talks
   to the core Python API with a bridged principal. So we reuse `kanban_db` (the
   library) from a new core router — not the plugin over HTTP.

## Scope

**In:** the project record and its membership; a `projects` HTTP surface on the
core API reusing `kanban_db` for all work state; a `/projects` list and a
`/projects/[slug]` detail page in `agent-home` with the six panels the request
names (files, resources, users, conversations, progress, goal); linking existing
objects into a project (a to-do, an arrival, a file, a goal, a conversation);
creating board cards from the project page and watching them run; and
`hermes projects …` CLI + skill so the agent reaches projects at footprint rung 2.

**Out, deliberately:**

- **A second execution engine.** No new dispatcher, no new worker spawn path, no
  new status vocabulary. A project's work *is* board cards.
- **Replacing the dashboard Kanban tab.** It stays as the operator's view. This
  is the user's view of the same rows (`AGENTS.md`: `agent-home` is the primary
  UI, the dashboard is the operator console — both are correct here).
- **A new core model tool.** Rung 2, as To-dos did.
- **Cross-profile *identity* work.** We rely on the already-decided shared
  GoTrue subject; we do not build a box-level user directory.
- **Migrating Kanban to Postgres.** Tempting (RLS for free) and wrong: it would
  fork the dispatcher, the worker handoff, the WebSocket tail and 8.8k LOC of
  tested behaviour for a benefit we can get at one API seam.
- **Realtime drag-and-drop parity.** Phones get a column-per-screen list with
  stage buttons, as `/todos` does; drag-and-drop is a desktop follow-up.

## 1. The record — where a Project lives

### The decision: promote `projects_db` to the shared root, beside the board

A project that exists to be worked on *with other profiles* cannot live in one
profile's `$HERMES_HOME`. Three options were considered:

| option | verdict |
|---|---|
| **A. Postgres `projects` table in the app schema** | **Rejected.** FG-27 makes the app schema profile-derived, so a Postgres project would belong to exactly one profile and a second profile would create its own invisible copy of the same project — the precise footgun FG-27 exists to close. RLS is not worth re-earning the cross-profile problem. |
| **B. New root-anchored store next to `projects.db`** | **Rejected.** That is a second project registry. The repo's rule from the To-dos plan holds: no additional store when an existing one has the right shape. |
| **C. Move the existing `projects.db` to the shared root** (`kanban_home()/projects.db`), keep its schema, add the collaboration tables | **Chosen.** It already carries `board_slug`, `primary_path`, folders and a slug; the board already carries `tasks.project_id` pointing at it; and `kanban_home()` is the same resolver the board uses, so the two are co-located and can never disagree about which root they are on. |

Option C changes the meaning of an existing store, so it is a migration, not a
comment: on first open of the root DB, import every profile's
`$HERMES_HOME/projects.db` rows (slug collisions get `-2`, and the imported row
records `imported_from_profile`), then leave the per-profile file in place,
untouched and unread. Nothing deletes a user's data to satisfy a refactor, and
the desktop keeps working while it is repointed. `projects_db.projects_db_path()`
gains the root resolution with `HERMES_PROJECTS_DB` as the explicit override
tests and odd deployments use, mirroring `HERMES_KANBAN_HOME`.

### Additive tables (same DB, same style as the board)

```sql
-- Who is in this project. A member is a *person* (GoTrue subject, box-wide
-- since the shared-GoTrue decision); a participant is a *profile* (an
-- instrument the work runs on). Both are needed and they are not the same
-- list: a project may run cards on the `research` profile that no human is
-- a member of, and a member may contribute with no profile of their own.
CREATE TABLE IF NOT EXISTS project_members (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',   -- lead | member | viewer
    added_by    TEXT,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS project_profiles (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    profile     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'contributor',  -- host | contributor
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, profile)
);

-- Everything the project gathers that lives in a *profile-scoped* Postgres
-- table: a file asset, an arrival, a to-do, a goal, a memory document, a
-- conversation. The row is a pointer, never a copy — the authority stays in
-- the profile that owns it, and `profile` is what makes the pointer
-- resolvable (FG-27: cross-profile reads are a fan-out, not a join).
CREATE TABLE IF NOT EXISTS project_links (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,   -- file | arrival | todo | goal | memory | session | url
    profile     TEXT NOT NULL,   -- '' for kind='url' and kind='session' on the shared root
    ref         TEXT NOT NULL,   -- the id in that profile's store
    label       TEXT,            -- cached display text, refreshed on read when resolvable
    added_by    TEXT,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, kind, profile, ref)
);
CREATE INDEX IF NOT EXISTS idx_project_links_kind
    ON project_links(project_id, kind, added_at DESC);

-- Additive columns on `projects`, in the projects_db migration style.
ALTER TABLE projects ADD COLUMN visibility            TEXT NOT NULL DEFAULT 'shared';
ALTER TABLE projects ADD COLUMN owner_user_id         TEXT;
ALTER TABLE projects ADD COLUMN status                TEXT NOT NULL DEFAULT 'active';
                                 -- planning | active | paused | done | archived
ALTER TABLE projects ADD COLUMN due_at                INTEGER;
ALTER TABLE projects ADD COLUMN summary               TEXT;   -- the agent's rolling "where this stands"
ALTER TABLE projects ADD COLUMN summary_at            INTEGER;
ALTER TABLE projects ADD COLUMN imported_from_profile TEXT;
```

`project_links.kind='goal'` is the join to FG-29: a project serves one or more
goals, and because the goal tree already rolls progress up by tier, the project
page can show *the* number that matters without inventing a second progress
model. The primary goal is the one flagged by ordering (first added), kept
simple until somebody needs more.

### Cards belong to a project through the column that already exists

`tasks.project_id` — already on the board, already used for worktree anchoring.
Two small additions to `kanban_db`, both additive and both used by the dashboard
too if it wants them:

- `list_tasks(..., project_id=…)` — one more `AND project_id = ?` clause in the
  existing builder. Without it every project read would filter in Python over
  the whole board.
- `CREATE INDEX idx_tasks_project ON tasks(project_id, status)` through the
  existing additive-index path (`_add_column_if_missing` neighbours), because a
  project view is by definition a per-project query.

**No new columns on `tasks`.** Everything else the page needs — comments,
attachments, runs, events, child progress, diagnostics — is already there and
already aggregated by the shapes `plugin_api.py` proved out.

## 2. Permissions — the honest version

The board is SQLite with no RLS, so enforcement is app-layer at exactly one
seam: the new router. The rule, stated once and tested as a negative matrix:

1. Every endpoint resolves the C1 principal through
   `_comms_resolve_principal(request, allow_as=True)`, as `/todos` and `/inbox` do.
2. A caller may read a project when they are the `owner`, or a row in
   `project_members`, or the project's `visibility = 'shared'` **and** the
   caller holds a `principals` row in a profile listed in `project_profiles`.
   Anything else is a 404 (not a 403 — the existence of a project is itself
   information).
3. Writes need `lead` or an instance `admin`/`owner`; `viewer` never writes.
4. **Board reads stay principal-filtered.** `kanban_db.list_tasks(principal=…)`
   already drops another user's `private:` cards; the router always passes the
   principal, never `None`. A project view must not become the way to read a
   private card.
5. **Linked Postgres rows are never read on the project's authority.** A
   `project_links` row is a pointer; resolving it re-reads the object through
   the owning profile's own API under the caller's own principal, so a file the
   caller cannot see in `/files` does not become visible because somebody linked
   it here. An unresolvable link renders as its cached `label`, greyed, marked
   "you don't have access" — visible as a fact, unreadable as content.

Rule 5 is the load-bearing one. It is what lets a shared board carry links into
profile-scoped, RLS-protected data without becoming a hole in it.

## 3. Cross-profile reads are a fan-out

`agent-home`'s `HermesApiClient` already binds a profile (`?profile=` on reads,
`profile` in bodies) and FG-28's console fan-out is the established pattern. So
the project detail read is:

```
GET /api/projects/acme-rollout            (agent-home BFF)
   ├─ core API  /api/registry/projects/acme-rollout        → record, members, profiles, links, board rollup
   ├─ per profile in project_profiles, in parallel, under the caller's principal:
   │     /api/registry/files?ids=…        → the file cards
   │     /api/registry/incomings?ids=…    → the arrivals
   │     /api/registry/todos?ids=…        → the to-dos
   │     /api/comms/goals                 → the goal + metrics
   └─ merge, tagging every item with the profile it came from
```

Fan-out is bounded by `project_profiles` (typically 1–3), runs in parallel, and
**degrades per profile**: one profile that is down or that the caller is not
enrolled in yields a panel section marked unavailable, not a 500 for the page.
That is the same "an uninitialised profile gets an empty page, not a 500"
posture the Incomings and To-dos surfaces settled on.

## 4. The API

New `hermes_cli/projects_api.py`, mounted in `web_server.py` beside the todos
and incomings routers, prefix `/api/registry/projects` (the Python surface keeps
the `registry` prefix; `agent-home` exposes `/api/projects/*`). It calls
`projects_db` and `kanban_db` directly — never the dashboard plugin.

| endpoint | purpose |
|---|---|
| `GET /` | projects the caller may read: `status`, `q`, `archived`, keyset `cursor` → `{items, next_cursor}`; each item carries the board rollup (`{todo, running, blocked, review, done}`), member count, primary goal progress, `due_at` |
| `POST /` | create: name, optional slug, description, icon/colour, `board_slug` (bind an existing board or create one with the project's slug), folders, first goal link |
| `GET /{slug}` | the record + members + profiles + board rollup + link ids grouped by kind + the last N `task_events` |
| `PATCH /{slug}` | name, description, status, `due_at`, icon/colour, visibility, `board_slug` |
| `POST /{slug}/members`, `DELETE /{slug}/members/{user_id}` | membership + role, from the FG-26 member roster |
| `POST /{slug}/profiles`, `DELETE /{slug}/profiles/{profile}` | which instruments the project runs on |
| `POST /{slug}/links`, `DELETE /{slug}/links` | attach/detach a file, arrival, to-do, goal, memory doc, conversation or URL |
| `GET /{slug}/board` | the columns for this project's cards — `list_tasks(project_id=…, principal=…)` plus the same link/comment/progress/diagnostics rollups `plugin_api.get_board` computes, factored into a shared helper so the two surfaces cannot drift |
| `POST /{slug}/cards` | create a card already carrying `project_id` (and therefore the project's worktree/branch convention), `assignee` = a profile in `project_profiles` |
| `PATCH /{slug}/cards/{task_id}` | title/body/priority/assignee/status through the existing `kanban_db` transitions — including its refusal to set `running` directly |
| `POST /{slug}/cards/{task_id}/comments` | the discussion thread that already exists |
| `GET /{slug}/cards/{task_id}` | the card, its runs, its comments, its attachments, its diagnostics |
| `GET /{slug}/activity` | the merged tail: `task_events` for the project's cards + C8 traces for its linked sessions, newest first |
| `GET /{slug}/conversations` | past conversations: sessions whose cards carry `project_id` (`tasks.session_id` already records the originating session) plus explicitly linked ones |
| `POST /{slug}/summarise` | ask the agent for the rolling "where this stands" paragraph; writes `summary`/`summary_at` |

**Shared rollup helper.** `plugin_api.get_board`'s aggregation (link counts,
comment counts, child done/total, diagnostics, latest run summary) moves into
`hermes_cli/kanban_view.py` and both routers call it. Copying 140 lines of
aggregation into a second surface is how two boards start disagreeing about what
"progress" means.

**Events.** The dashboard tails `task_events` over a WebSocket. `agent-home` gets
`GET /{slug}/events?since=<event_id>` (long-poll/SSE through the BFF), because a
project page on a phone that silently goes stale is worse than one that costs a
poll every few seconds. `latest_event_id` is returned by `/board` so the client
knows where to start — the same contract the plugin already uses.

## 5. The page

`/projects` and `/projects/[slug]` in `agent-home`, server-rendered first paint
under the resolved principal, `data-component` on every component root, filter
state in the URL, `loading.tsx` skeletons, `BusyRegion` around anything that
mutates — the conventions `/todos` and `/inbox` settled.

### `/projects` — the list

```
▣  Acme rollout                                    due Fri   ●●●○○  62%
   4 members · 2 profiles · 3 running, 1 blocked · goal: Land Q3 revenue
   ─────────────────────────────────────────────────────────────────────
▣  Website refresh                                          ●●○○○  31%
   2 members · 1 profile · 2 todo · goal: Ship the new site
```

Chips: Active · Planning · Paused · Done · All. One number per project — the
primary goal's progress, not a card count, because "8 of 19 cards" is a
measure of decomposition, not of progress.

### `/projects/[slug]` — the one place

A single scrollable page on a phone with a sticky segmented control, a
two-column layout from `md:` up. **Not tabs that hide things**: the request is
to see it all in one place, and a tab is a place to hide a panel.

```
┌─ Header ────────────────────────────────────────────────────────────┐
│ ▣ Acme rollout            active · due Fri · lead: Leo   [ ⋯ ]      │
│ "Where this stands: the quote is signed; waiting on legal." (agent) │
└─────────────────────────────────────────────────────────────────────┘
[ Progress ] [ Board ] [ Files ] [ Resources ] [ People ] [ Conversations ]

Progress     the primary goal, its metrics and its trend (FG-29 rollup);
             the card rollup per column; blocked cards first, because a
             blocked card is the only thing on this page that is asking
             for a human right now.
Board        the project's cards, one column per screen on a phone with
             ‹ › between columns; each card shows assignee-profile, run
             state, comment count, child progress N/M, diagnostics badge.
             Tapping a card opens `/projects/[slug]/cards/[id]`.
People       members (avatar, role, from `/members`) and participating
             profiles (name, description, what it is running now) in one
             list, because "who is on this" means both.
Files        `/files` cards for linked assets + every `task_attachments`
             blob on the project's cards, in one grid. A file that arrived
             on WhatsApp and a file a worker attached to a card are the
             same kind of thing to the person looking for it.
Resources    linked arrivals (quoted, linking to `/inbox/<id>`), linked
             memory documents, linked to-dos with their stages, and plain
             URLs. Each row carries the profile it lives in.
Conversations sessions that produced or were started from this project's
             cards, plus linked ones: title, when, message count, link
             into `/chat/<id>`; and the C8 trace tail for anything a
             worker ran.
```

**Adding to a project is a link, from both ends.** `/projects/[slug]` has an
"Add" sheet (search across files, arrivals, to-dos, goals, conversations); and
`/todos/[id]`, `/inbox/[id]`, `/files` detail each gain an "Add to project"
action, which is where the user actually is when they realise something belongs
to a project. Two-way, as `/inbox` ↔ `/todos` already is.

**Creating work.** "New card" from the Board panel; and a to-do can be promoted
into a card, which is the missing half of the To-dos plan's deliberate "Kanban
stays out of it" — a *human* says a to-do needs decomposition, and this is the
button that says it.

### Navigation

The phone primary bar is full and was just re-budgeted for To-dos
(Home · To-dos · Chat · Inbox · Memory); Projects goes to `SECONDARY_NAV`
(sidebar + More sheet on a phone) and gets a first-class card on `/` Home
showing the active projects with their progress. Recommendation, not a decision
— see open question 3.

### Components

```
src/app/projects/page.tsx                     list, server-rendered
src/app/projects/loading.tsx
src/app/projects/[slug]/page.tsx              detail
src/app/projects/[slug]/loading.tsx
src/app/projects/[slug]/cards/[id]/page.tsx   one card
src/app/api/projects/**                       BFF mirror, one route per endpoint
src/components/projects/ProjectsList.tsx      + ProjectCard, ProjectsFilters
src/components/projects/ProjectDetailView.tsx orchestrates the panels
src/components/projects/panels/{Progress,Board,People,Files,Resources,Conversations}Panel.tsx
src/components/projects/BoardColumn.tsx       + BoardCard, CardDetailView
src/components/projects/AddToProjectSheet.tsx reused by /todos, /inbox, /files
src/components/projects/filters.ts            URL codec — a *server-safe* module,
                                              not "use client" (the /todos
                                              production-only boundary bug)
```

Client methods on `HermesApiClient`: `projects()`, `project()`, `createProject()`,
`updateProject()`, `projectBoard()`, `projectCard()`, `createProjectCard()`,
`updateProjectCard()`, `commentOnProjectCard()`, `projectMembers()`,
`addProjectMember()`, `addProjectProfile()`, `linkToProject()`,
`unlinkFromProject()`, `projectActivity()`, `projectConversations()`,
`projectEvents()`; types in `src/types/index.ts`.

## 6. The agent's route in (footprint rung 2)

`hermes projects` extends the existing `hermes project` command tree rather than
forking it: `list`, `show`, `create`, `link`, `members`, `cards`, `card add`,
`summarise`. Plus `skills/projects/SKILL.md` telling the agent when to open a
project (a piece of work that spans sessions, people or profiles), when to link
rather than copy, and that it may propose a project but the user creates one.
No new model tool.

## 7. Failure modes to design against

1. **Projects becoming a second to-do list.** A project is for work that spans
   sessions and people; a to-do is one decision. The list shows goal progress,
   not card counts, precisely so a project with no goal looks unfinished — which
   it is.
2. **The board and the project page disagreeing.** Prevented by one aggregation
   helper (`kanban_view.py`) and by never storing a derived count.
3. **Link rot.** A linked row is deleted in its profile: the pointer resolves to
   nothing and renders from `label` as "no longer available", never as an error.
   A sweep in the digest cron drops pointers unresolvable for 30 days.
4. **Fan-out cost.** Bounded by `project_profiles`, parallel, per-profile
   degradation, and the first page is server-rendered so the phone pays once.
5. **The permission seam being bypassed.** The only defence is that there is one
   router; the negative matrix in Testing is what keeps it true.

## 8. Testing

- **Store:** the root migration is idempotent and imports per-profile rows with
  slug-collision suffixes; an existing `projects.db` opens unchanged; the
  additive columns default so old rows read back identically.
- **Membership:** owner, `lead`, `member`, `viewer`, non-member, and a member of
  a *different* project — read/write matrix, with the non-member getting 404.
- **Board reuse:** `list_tasks(project_id=…)` returns only the project's cards;
  `principal=` still hides another user's `private:` card **through the project
  endpoint** (the regression that would matter most).
- **Link resolution:** a link to a file the caller cannot read renders as a
  greyed label and never leaks content; a link into a profile the caller is not
  enrolled in yields "unavailable", not a 500; a deleted target degrades.
- **Fan-out:** one profile down → that panel section is marked unavailable and
  the rest of the page renders.
- **Cards:** a card created from a project carries `project_id` and lands on the
  project's branch convention; `status='running'` is still refused directly;
  events appear in `/activity` with `latest_event_id` advancing.
- **Frontend:** the list default shows active projects; URL filter state
  round-trips; the detail page renders all six panels from one fetch; the
  server/client boundary test covers `filters.ts`; existing nav tests updated.
- **Live (systest box):** create a project bound to a new board, add a second
  profile, create a card, watch the gateway dispatcher pick it up and the run
  appear in the Conversations panel; link a real WhatsApp arrival and its
  attachment and confirm both render; confirm a second principal who is not a
  member gets a 404.

## 9. Sequencing

Each step is one PR against `develop`, rebased before push.

1. **Store** — root-anchored `projects_db` + the import migration + the three
   new tables + the additive columns; `list_tasks(project_id=…)` and its index.
   Nothing uses it yet.
2. **Board view helper** — extract `kanban_view.py` from `plugin_api.get_board`
   and repoint the dashboard plugin at it. Pure refactor, no behaviour change,
   landed alone so a regression here is unambiguous.
3. **API** — `hermes_cli/projects_api.py` + the permission matrix + fan-out
   resolution; mounted in `web_server.py`.
4. **BFF + client** — `agent-home` `/api/projects/*`, client methods, types.
5. **List page** — `/projects`, the Home card, the nav slot.
6. **Detail page** — the six panels, the card detail route, `AddToProjectSheet`
   and the "Add to project" actions on `/todos`, `/inbox`, `/files`.
7. **CLI + skill** — `hermes projects …` and `skills/projects/SKILL.md`.
8. **Events + summary** — the `since=` tail and `POST /summarise`.

Steps 1–3 are backend-only and can land first so that by the time step 6 renders
a panel there is a real project with real cards behind it — the sequencing that
worked for Incomings and To-dos.

## 10. Decisions taken

1. **Reuse the kanban board as the execution substrate; add no engine.** The
   dispatcher, the statuses, the runs, the comments, the attachments and the
   diagnostics are all already there and already tested.
2. **Promote the existing `projects_db` to the shared root rather than create a
   store.** A per-profile project cannot be a cross-profile project, and two
   project registries would be worse than one moved file.
3. **Members are people (GoTrue subjects); participants are profiles.** Both
   lists, because the board assigns work to profiles and the request is to
   collaborate with people *and* profiles.
4. **Permission enforcement is app-layer at one router seam,** with linked
   Postgres rows always re-read under the caller's own principal. The board has
   no RLS and this plan does not pretend otherwise.
5. **Cross-profile aggregation is a bounded fan-out with per-profile
   degradation,** never a cross-schema join (FG-27).
6. **One shared board-rollup helper** for the dashboard and `agent-home`.
7. **Panels, not tabs that hide.** "All in one place" is the requirement; a tab
   that hides the files is a smaller version of the problem we are fixing.
8. **Projects takes a secondary-nav slot plus a Home card,** because the primary
   bar was just budgeted for To-dos.

## 11. Open questions for the owner

1. **Board binding.** One board per project (created with the project, simplest
   mental model), or may several projects share a board (a repo with three
   workstreams)? Recommend **one board per project, with `board_slug` still
   settable** so an existing board can be adopted — `tasks.project_id` makes
   sharing technically fine, but "the project's board" is much easier to explain.
2. **Who may create a project?** Any member, or `admin`/`owner` only? Recommend
   any member creates, only `lead`/`admin` adds *profiles* (adding a profile
   means spending that profile's tokens).
3. **Nav.** Secondary nav + a Home card as recommended, or should Projects take
   a primary-bar slot (and if so, from which — Memory is the least daily of the
   five)?
4. **Cross-profile links by default.** Should linking an object from profile B
   into a project require B's admin's consent, or is the caller's own read
   access in B sufficient (recommended: sufficient — the link grants nothing the
   caller does not already have)?
5. **The desktop's projects.** After the move, do we repoint the Electron app
   and `hermes project` at the root store in the same PR (recommended, so there
   is one project list on the box), or leave the desktop on its per-profile file
   until it is next touched?
6. **Progress definition.** Is a project's headline number the primary goal's
   metric progress (recommended, and honest), or the card done-ratio (familiar
   from Kanban, and easy to game by splitting cards)?
