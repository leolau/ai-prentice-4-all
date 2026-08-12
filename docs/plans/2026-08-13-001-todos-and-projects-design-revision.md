---
title: "design: To-dos ed.2 and Projects ed.2 — the gaps, the seam between them, and every open question answered"
status: draft — spec for review
date: 2026-08-13
type: design revision
target_repo: ai-prentice-4-all
origin: user request — "Another agent is working on another part of the system. I want you to focus on the to-do page and project page design."
supersedes_sections:
  - docs/plans/2026-08-11-001-todos-staging-layer-plan.md §5, §6, §8, "Open questions" (the CLI, /start, the detail panel, the badge)
  - docs/plans/2026-08-12-001-projects-page-plan.md §11 (all six questions now answered as defaults)
depends_on:
  - hermes_cli/todo_store.py (shipped — the store this designs against, not around)
  - hermes_cli/todos_api.py (shipped — 8 routes; this adds 3)
  - hermes_cli/kanban_db.py (the execution substrate a promoted to-do lands on)
  - hermes_cli/projects_db.py (the record Projects promotes to the shared root)
  - cron/scheduler.py (the session-spawn path `/start` reuses rather than reinvents)
---

# To-dos ed.2 + Projects ed.2

Two plans are already written and one of them is built. This document does not
restate either. It does three things they cannot do from where they sit:

1. **Closes the To-dos gaps** — the four pieces of the To-dos plan that did not
   land, designed at the level of "someone can now implement this without asking
   a question": the agent's route in, `/start`, the detail panel's missing half,
   and the badge.
2. **Designs the seam between the two pages**, which neither plan could: To-dos
   deliberately said "Kanban stays out of it", Projects said a to-do "can be
   promoted into a card". That is one mechanism, described from two sides, and
   nobody has specified it.
3. **Answers all eleven open questions** (five in To-dos, six in Projects) as
   *defaults with a stated cost of reversal*, so implementation is not blocked
   on a review round. Every one is marked **default** — a default is a decision
   somebody can change in one line, not a decision that was avoided.

## Part 0 — Where the two features actually stand

Ground truth, from the tree at `develop`, not from the plans:

| To-dos plan step | state | evidence |
|---|---|---|
| 1 Store | **shipped** | `hermes_cli/todo_store.py` — additive columns on FG-06 `tasks`, `LIVE_STAGES = (staged, open, working)`, `STAGE_STATUS` written in one statement, partial-unique dedupe, `task_transitions` audit, `expire_staged(older_than_days=14)` |
| 2 Bridge | **shipped** | `custom/shared/todo_registration.py`, the `todos` triage handler, `todo-decisions.md` × 3 skills, per-batch cap, expiry in `digest_cron_v2.py` |
| 3 Notify | **shipped** | `hermes_cli/todo_notifier.py` — FG-10's first production producer |
| 4 API | **shipped, 8 of 11 routes** | `hermes_cli/todos_api.py`: `GET ""`, `/facets`, `POST ""`, `GET /{id}`, `PATCH /{id}`, `/{id}/stage`, `/{id}/complete`, `/{id}/snooze`. **Missing: `/start`, `/advance`.** `/promote` and `/dismiss` were correctly collapsed into one `/stage` — that is a simplification, not a gap. |
| 5 Page | **shipped** | `/todos`, `/todos/[id]`, `loading.tsx`, 5 components, URL filter state, primary-nav slot |
| 6 Outgoing seam | **shipped** | `hermes_cli/todo_outbound.py` — proposes, never sends |

| To-dos gap | why it matters |
|---|---|
| **No `hermes todos` CLI, no skill** | The plan's own footprint rung 2. Today the *agent* cannot see or touch a to-do at all: the page and the HTTP API can, the model cannot. Every "the agent creates a to-do" sentence in the plan is currently only true of the triage bridge. This is the largest gap by far. |
| **No `/start`** | `/stage {"stage":"working"}` moves the row. Nothing spawns the session. "Work on this" is a state change with no work in it. |
| **No `/advance`** | FG-06 progress states have no driver. Low cost to skip; see Part 1.4. |
| **Detail panel: no linked memory doc** | `todos_api.get_todo` resolves `history` + `source` (the arrival). The plan also promised the memory document the arrival produced. |
| **`/files` has no back-link** | `/inbox/[id]` links out to `/todos?source_ref=…` (`IncomingDetailView.tsx:164`); the files detail surface never got the same. |
| **Nav has no open count** | The plan called the badge "the honest answer to *does this system have anything for me right now*". `nav-items.ts` has no count mechanism at all. |

Projects: **nothing built.** Plan reviewed and merged (PR #204); no store change,
no router, no page. Its sequencing is unchanged by this document — only its open
questions are resolved, and one step is added (Part 2).

## Part 1 — To-dos ed.2

### 1.1 `hermes todos` + `skills/productivity/todos/SKILL.md` (rung 2)

This is the one gap that changes what the product *is*: until it lands, a to-do
is something the user reads and the pipeline writes, and the agent — the thing
the whole repo is about — is not a participant.

**Where it goes.** A new `hermes_cli/todos_cmd.py` registered in
`hermes_cli/main.py` beside `incomings` and `goal`, following the
`goal_tree_cmd.py` shape exactly (`register_*_subparser(subparsers)`, one
`async def _verb(...)` per subcommand, `_run(coro)` bridging to sync,
`--as <user>` for the actor). It calls `todo_store.TodoStore` **directly**, not
over HTTP: the CLI runs on the box with the datastore router already resolved,
and going through `todos_api` would mean minting a token to talk to ourselves.

```
hermes todos list    [--stage staged,open,working] [--priority high] [--q TEXT]
                     [--source-kind inbound] [--limit N] [--json]
hermes todos show    <id>            # + history + the source arrival
hermes todos add     "<title>" [--why TEXT] [--priority p] [--due YYYY-MM-DD]
                                     [--stage staged|open] [--goal <goal_id>]
hermes todos stage   <id> <stage> [--outcome TEXT]
hermes todos start   <id> [--session]        # Part 1.2
hermes todos done    <id> [--outcome TEXT] [--propose-reply]
hermes todos snooze  <id> --until <when>
hermes todos facets                  # the same counts the chips use
hermes todos expire  [--days 14] [--dry-run]   # what the digest cron calls
hermes todos backfill --since <date>           # To-dos Q4's escape hatch
```

Every verb maps 1:1 onto a `TodoStore` method that already exists
(`list`, `get`+`history`, `create`, `set_stage`, `update`, `snooze`, `facets`,
`expire_staged`). `add` and `stage` are the only writes the skill encourages;
the rest is reading.

**`--json` on every read.** The agent parses; the human reads the table. One
flag, both audiences, no second command tree.

**The skill is the interesting half.** `skills/productivity/todos/SKILL.md`
(under `productivity/`, beside `note-taking`) states the four rules that keep the
agent from turning the page into noise — the same failure mode the To-dos plan
spent §7 on, now reachable by a model that can write rows in a loop:

1. **A to-do is a decision for the user, not a note to self.** In-session
   planning stays in `tools/todo_tool.py`. If nobody needs to decide anything,
   it is not a to-do.
2. **Before spending a model call on unrequested work, create the to-do first**
   — the plan's §3.2 rule, restated where the agent will actually read it.
3. **`staged` by default from an agent.** Only the user's own request, or an
   explicit deadline, justifies `open` (which notifies). The agent does not get
   to ring the bell.
4. **One to-do per decision.** Check `hermes todos list --q` before adding; the
   partial-unique index will collapse an exact duplicate, but a near-duplicate
   with a reworded title will not be caught by the store and must be caught here.

No new core model tool. `hermes todos --help` plus the skill is the whole
interface, which is the same route `incomings` and `goal` took.

### 1.2 `POST /{id}/start` — the session spawn

The plan's §8. Today the button is a lie of omission: it moves the stage and
nothing starts.

```
POST /api/registry/todos/{id}/start
     { "session": true, "profile": "<optional>" }
  → { ...todo, "session_id": "<id>|null", "spawned": bool }
```

Behaviour:

1. `set_stage(principal, id, "working", actor="user:<id>")` — the state change
   happens first and independently, because a to-do the user said they are
   working on must not stay `open` because a spawn failed.
2. If `session: false` (the CLI's default, and the page's default on a phone),
   stop there. Moving to `working` without starting a robot is a legitimate act:
   the user is doing it themselves.
3. If `session: true`, build the seed prompt from the to-do (title, description,
   `source_note`), the source arrival's body when `source_ref` resolves, and its
   attachments as file paths — then spawn through **the path `cron/scheduler.py`
   already uses** (`AIAgent` construction at `cron/scheduler.py:2875`), not a new
   one. The extraction is the work: that call site is inside a scheduler
   function, so it moves to a small `spawn_seeded_session(prompt, *, profile,
   origin)` helper that both callers use. One spawn path on the box, or we will
   have two that drift on `HERMES_HOME` resolution.
4. Record `session_id` on the transition row (`task_transitions` already carries
   the actor; the session id goes in its note), so `/todos/[id]` can link into
   `/chat/<id>` and the C8 trace tail in `/activity`.
5. **The spawn is best-effort and reports itself.** `spawned: false` plus an
   `error` field, never a 500 — the same posture `todo_outbound` took for a
   malformed draft: losing the state change because the subprocess failed is the
   wrong trade.

**Why the profile is a parameter.** FG-28: a to-do raised on the `personal`
profile may be work for `research`. Default is the caller's bound profile; an
explicit `profile` must be one the caller holds a `principals` row in.

### 1.3 The detail panel's missing half, the `/files` back-link, the badge

**Linked memory document.** `get_todo` gains one more best-effort resolution
beside `_source_item`, with the identical contract (returns `None` on any
failure, never raises, never blocks the payload): when the source arrival has
`remembered_at` set (`inbound_registry` already carries `remembered_at` /
`remembered_by` / the `remembered` property), resolve the memory document it
produced and return it as `payload["memory"]`. `TodoDetailView` renders it as one
row under the quoted arrival — "◇ remembered as *<title>*" linking to
`/memory?doc=<id>`. If the arrival was never remembered the key is absent and the
row is not rendered; a to-do whose provenance is thin renders thin.

**`/files` back-link.** `IncomingDetailView.tsx:164` is the pattern:
`/todos?source_ref=<arrival id>&stage=staged,open,working,done,dismissed`. The
files detail surface gets the same link whenever the asset's arrival raised a
to-do. Deliberately a *link*, not a count: a count needs a second query per
card, and the plan's own two-way-link requirement is satisfied by the link.

**The badge.** `nav-items.ts` is a static array today and must stay one — it is
imported by server components, and the `/todos` `filters.ts` incident (a client
module crossing into a server component as a proxy in production only) is the
standing reminder of what happens when that boundary is treated casually. So:

- `NavItem` gains an optional `badge?: "todos-open"` — a *name*, not a number.
  The array stays a plain, server-safe constant.
- The count arrives from one place: `client.todosFacets()` already returns
  per-stage counts. The app shell (already a server component, already resolving
  the principal) reads it once and passes `{ "todos-open": n }` into
  `BottomNav`/sidebar as a prop.
- Zero renders nothing. A badge that says `0` is a small daily lie about there
  being something to look at.
- Cost is one extra facets call per shell render, on a query already indexed by
  `tasks_owner_stage_idx`. If that ever shows up, it caches for 30 s — not
  before, because a stale badge is worse than a cheap one.

### 1.4 `/advance` — deliberately deferred, and why

The plan listed `POST /{id}/advance` for FG-06 progress-state ladders. It should
stay unbuilt until something asks for it: `todo_store` supplies the trivial
`('captured' → 'done')` ladder, no shipped surface renders intermediate states,
and no producer writes one. Building the endpoint now means designing the state
vocabulary before there is a consumer to constrain it — the repo's
"speculative infrastructure" rejection, applied to our own plan. **Default:
deferred**, and the To-dos plan's §5 row is amended to say so rather than
sitting there looking unfinished.

## Part 2 — The seam: a to-do becomes a project card

Neither plan owns this. To-dos §8 says Kanban stays out, "a human can say so".
Projects §5 says "a to-do can be promoted into a card". Both are right, and the
mechanism is one paragraph that nobody has written.

### The rule

**A to-do is one decision; a card is a piece of work that may take several
sessions and several profiles. Promotion is the human saying "this turned out to
be the second kind."** Only a human promotes. Never triage, never the agent, and
no threshold, no heuristic, no "if the description is longer than N". An
automatic bridge from a misread sentence into a swarm board is how a
one-sentence email spends a day of tokens.

### What promotion does

`POST /api/registry/projects/{slug}/cards` gains an optional
`from_todo: {profile, id}`:

1. Read the to-do under **the caller's own principal** in the named profile
   (Projects §2 rule 5 — this is a linked-object read, not a project-authority
   read). Not readable → 404, and no card.
2. Create the card with `project_id` set, `title`/`body` seeded from the to-do,
   `priority` mapped (`critical|high → high`, `normal|low → normal` — the board's
   vocabulary is coarser and pretending otherwise invents a third scale),
   `assignee` = a profile in `project_profiles`, `status='triage'`. Never
   `ready`: promotion is not dispatch. A human still moves it.
3. Write a `project_links` row `kind='todo'`, `profile=<the to-do's profile>`,
   `ref=<todo id>`, so the project page shows the to-do it came from and the
   provenance survives.
4. Move the to-do to `working` with `actor="user:<id>"` and a transition note
   naming the card. **Not `done`** — the work is not finished, it moved. And not
   left `open` either, or it sits on the To-dos page asking for a decision that
   has already been made.
5. The card's `body` ends with a line pointing back at the to-do id, so the
   worker that picks it up can read its origin.

### What it does *not* do

- **No reverse sync.** Closing the card does not close the to-do; the to-do is
  closed by a human, on the To-dos page, when they agree it is done. Two-way
  state sync between two stores with different vocabularies is a class of bug we
  are choosing not to open.
- **No `project_id` column on to-dos.** The pointer lives in `project_links`
  (Projects' link table, already designed for exactly this), so the FG-06 `tasks`
  table learns nothing about a SQLite board on the root, and the profile-scoped
  store stays independent of the shared one. The direction of the dependency
  matters: Projects knows about to-dos; to-dos do not know about Projects.
- **No automatic project creation.** Promotion requires an existing project.

### Where the button is

`/todos/[id]` gains "Promote to a project card" in the action row, opening the
same `AddToProjectSheet` Projects §5 already specifies (project picker, then
profile picker from `project_profiles`). One sheet, two uses: linking a to-do to
a project as *context*, and promoting it into *work*. The page states the
difference in one line, because a user who picks the wrong one gets a card they
did not want: **link** keeps it a to-do; **promote** makes it a card and moves
the to-do to `working`.

This also slots into Projects' sequencing as **step 6b**, after the detail page
exists — a promote button with no project page to land on is a trapdoor.

## Part 3 — Every open question, answered

Defaults. Each row says what changes if Leo decides otherwise, so overriding one
is a one-line instruction and not a re-plan.

### To-dos (plan §"Open questions")

| # | question | **default** | cost of changing later |
|---|---|---|---|
| 1 | The bar — conservative or generous | **Conservative**: only an explicit request addressed to the user, from a known contact, or a stated deadline clears into `open`; everything else `staged`. | One edit to three `todo-decisions.md` files, hot-reloaded, no deploy. This is the cheapest reversible decision in the whole feature — which is exactly why it was put in a skill file. Free. |
| 2 | `staged` expiry | **14 days**, as shipped (`DEFAULT_STAGED_EXPIRY_DAYS`), auto-`dismissed` with actor `system:expiry`. | A config value. Free. Note the sweep dismisses rather than deletes, so a too-short window loses nothing but visibility. |
| 3 | Nav | **Confirmed as shipped**: Home · To-dos · Chat · Inbox · Memory; Graph secondary. | An array edit + the `BottomNav` test. Cheap. |
| 4 | Backfill `wa_tasks`/`email_tasks` | **Start empty.** Those rows were extracted under a bar that did not exist; importing them fills the first-run page with exactly the noise §7 is about. `hermes todos backfill --since` (Part 1.1) stays available for a deliberate, dated sweep. | Free — the command exists either way. |
| 5 | One owner or per-member | **One owner principal**, as the arrival registry does, until a second member has their own channel. | Real cost: the bridge would need per-member resolution, and every `staged` row already written would belong to the wrong principal. Revisit *before* a second member is enrolled, not after. This is the only To-dos question with a non-trivial reversal cost. |

### Projects (plan §11)

| # | question | **default** | cost of changing later |
|---|---|---|---|
| 1 | Board binding | **One board per project**, created with the project's slug; `board_slug` remains settable so an existing board can be adopted, and `tasks.project_id` means sharing keeps working for anyone who wants it. | Free. Nothing in the schema forbids sharing; this is a default in the create path and a sentence in the UI. |
| 2 | Who may create | **Any member creates a project; only `lead`/`admin` adds a *profile* to one.** Creating a project spends nothing; adding a profile spends that profile's tokens on the dispatcher's next tick, and that is the act worth gating. | Free — both are checks in the one router seam. |
| 3 | Nav | **Secondary nav + a first-class Home card.** The primary bar was budgeted for To-dos two days ago and re-litigating it before Projects has any real data would be churn. The Home card is the actual discovery path. | Cheap (array + tests). Worth revisiting once projects exist and we can see whether it is a daily surface. |
| 4 | Cross-profile links and consent | **The caller's own read access in the target profile is sufficient.** A `project_links` row grants nothing: resolution always re-reads under the caller's principal (§2 rule 5), so a link is a bookmark, not a grant. Requiring a profile admin's consent to bookmark something you can already read is friction that buys no security. | Free to add consent later; the link table already records `added_by`. |
| 5 | The desktop's projects | **Repoint the Electron app and `hermes project` at the root store in the same PR as the move** (Projects step 1). Two project lists on one box is a support problem that reads as data loss to the user. The per-profile file is left in place, imported and unread, so the migration is reversible by pointing `HERMES_PROJECTS_DB` back. | High if deferred: every day both stores are live, new rows land in the old one and the import has to run again. Do it once, in the same PR. |
| 6 | Progress definition | **The primary goal's metric progress**, with the card rollup shown *beside* it (`3 running · 1 blocked · 8/19 done`) but never as the headline number. A done-ratio is a measure of decomposition and moves when you split a card. A project with no linked goal shows "no goal set" — not `0%`, and not a card ratio quietly standing in for one. | Free — both numbers come from the same read. |

## Part 4 — What this adds to the two sequences

**To-dos ed.2**, in dependency order; each step one PR against `develop`:

| step | scope | note |
|---|---|---|
| **7** | `hermes_cli/todos_cmd.py` + `skills/productivity/todos/SKILL.md` | The gap that matters. Independent of everything else; can land immediately. |
| **8** | `spawn_seeded_session()` extracted from `cron/scheduler.py`, then `POST /{id}/start` + the page's "Work on this" | Extraction lands with the endpoint that needs it, so the refactor has a caller and a test. |
| **9** | `payload["memory"]` on `get_todo` + the `TodoDetailView` row + the `/files` back-link | Small, cosmetic, one PR. |
| **10** | `badge?: "todos-open"` + the shell's facets read | Touches nav tests; landed alone so a nav regression is unambiguous. |

**Projects** — sequencing unchanged (store → `kanban_view.py` → API → BFF → list
→ detail → CLI/skill → events/summary), plus:

| step | scope |
|---|---|
| **6b** | `from_todo` on `POST /{slug}/cards`, the `/todos/[id]` promote action, and the link-vs-promote copy (Part 2) |

Projects step 1 now also carries the desktop/`hermes project` repoint (Q5).

## Part 5 — Testing these additions

Behaviour contracts, not change detectors.

- **CLI:** `hermes todos add --stage open` writes exactly one row and one
  transition with actor `agent:*`/`user:*`; `--json` output round-trips through
  `json.loads` for every read verb; `list --stage` rejects an unknown stage
  rather than returning everything; `expire --dry-run` writes nothing; `--as`
  against a principal the caller may not act as is refused.
- **`/start`:** `session: false` moves to `working` and spawns nothing;
  `session: true` records a `session_id` on the transition; **a spawn failure
  still leaves the to-do `working` and returns `spawned: false`** (the one that
  matters); a `profile` the caller does not hold is refused; the seeded prompt
  contains the arrival body when `source_ref` resolves and does not blow up when
  it does not.
- **Detail additions:** an arrival never remembered omits `memory` entirely
  (absent, not `null` with a broken row); an unreachable memory tier degrades to
  absent; the `/files` back-link appears only when the asset's arrival raised a
  to-do.
- **Badge:** zero open renders no badge; the count matches `facets()`;
  `nav-items.ts` stays importable from a server component (the `filters.ts`
  boundary regression test, extended).
- **Promotion:** promoting creates a card with `project_id` and `status='triage'`
  (never `ready`), writes the `project_links` row, and moves the to-do to
  `working` — not `done`; a to-do the caller cannot read in the named profile
  yields 404 **and no card** (the partial-failure case: no orphan card, no moved
  to-do); closing the card leaves the to-do alone; promoting the same to-do twice
  is refused by the `project_links` primary key rather than making a second card.
- **Live (systest box), after deploy:** promote a real triage-born to-do into a
  project card, watch the gateway dispatcher pick it up under the assigned
  profile, and confirm the run appears in the project's Conversations panel while
  the to-do reads `working` with a transition naming the card.

## Part 6 — Decisions taken here

1. **The agent's route into to-dos is a CLI plus a skill, calling the store
   directly** — rung 2, no core tool, no self-HTTP.
2. **`/start` separates the state change from the spawn.** The stage moves
   first, unconditionally; the spawn is best-effort and reports itself.
3. **One session-spawn path on the box.** `cron`'s is extracted and shared, not
   copied.
4. **`/advance` is deferred** until a producer of intermediate progress states
   exists.
5. **The nav badge is a name in a static array plus a count passed as a prop.**
   `nav-items.ts` stays server-safe; zero renders nothing.
6. **Promotion to a card is human-only, one-way, and pointer-based.** No
   heuristic, no reverse sync, no `project_id` on `tasks`. Projects depends on
   to-dos; to-dos never learn about Projects.
7. **All eleven open questions are answered as defaults**, each with its cost of
   reversal stated. The two worth arguing about before code lands are To-dos Q5
   (one owner principal — expensive to change once rows exist) and Projects Q5
   (repoint the desktop in the same PR — expensive to defer). The other nine are
   free to change later and should not hold up implementation.
