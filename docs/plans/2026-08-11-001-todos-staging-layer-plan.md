---
title: "feat: To-dos — the staging layer between what arrives, what the system analyses, and what the user decides to do"
status: draft — spec for review
date: 2026-08-11
type: feature
target_repo: ai-prentice-4-all
origin: user request — "when the triage picks up incoming messages it should … create an entry in the to-dos and notify the user … all incoming will be staged and any in-depth analysis and work will create a to-do entry so that user knows about it and can decide to work on it or not. Once work is completed, it can also trigger out-going actions … I need a new TO-DOs page in agent-home … one of the core infrastructure to bridge automation, incoming and outgoing, analysis and user intervention"
depends_on:
  - docs/design/task-and-todo-structures-inventory.md (what already exists)
  - docs/plans/2026-08-10-001-unified-incomings-inbox-plan.md (the arrival registry this reads from)
  - docs/design/TRIAGE_SKILLS_SYSTEM.md (the write path)
---

# To-dos

> **Amended by** `docs/plans/2026-08-13-001-todos-and-projects-design-revision.md`
> (ed.2): steps 1–6 below shipped; the CLI + skill, `POST /{id}/start`, the
> linked memory document, the `/files` back-link and the nav badge are designed
> there, `/advance` is deferred there, and all five open questions below are
> answered there as defaults. §9's `hermes todos send` — the command
> `todo_outbound.command_for()` already writes into every approval — is pulled
> forward into that CLI rather than left to the follow-up FG.

## What this is for

Everything in the system today runs in one of two modes. Either it is fully
automatic and invisible — triage classifies a message, a fact lands in
`MEMORY.md`, an arrival is mirrored into `inbound_items` — or it is fully
manual, because the user happened to be in a chat at the time. There is no
third mode: *the system noticed something, formed a view about what should
happen next, and is waiting for a human to say go.*

That third mode is what a to-do is. It is deliberately not a task manager
feature; it is the join between four things the codebase already has and
cannot currently connect:

```
   incoming                 analysis                decision              outgoing
   ────────                 ────────                ────────              ────────
   inbound_items    →   triage skills say      →   TO-DO        →   approved action
   (shipped)            "this needs work"          (this plan)       (reply, invite,
   file_assets          skills/RAG                 user picks:       schedule, escalate)
   (shipped)            (shipped)                  do / defer /      via FG-10 + C4
                                                   drop              (this plan opens
                                                                      the seam)
```

Requirement 1 asks for step (iii) of the triage sequence — register (done),
decide what to remember (done), **create a to-do and notify**. Requirement 2
says the point of staging is that nothing deep happens without the user
knowing. Requirement 3 asks for the surface. Requirement 4 is the reason the
first three are one piece of work and not three: the to-do is the object that
makes automation legible.

## What already exists, and what this does with it

The inventory (`docs/design/task-and-todo-structures-inventory.md`) found six
task-shaped structures and concluded the gap is not a data model. That
conclusion drives the central decision here.

| structure | what happens to it in this plan |
|---|---|
| `tools/todo_tool.py` — the agent's in-session scratchpad | **untouched.** Session plans are not user to-dos; promoting them would fill the list with the model's own bookkeeping. |
| Kanban (`hermes_cli/kanban*.py`) | **untouched.** Multi-worker execution of dev work, on box-local SQLite, no C2 scoping. A to-do may *start* a worker later; it does not become a card. |
| `tasks` / `task_progress_states` / `task_transitions` (FG-06, Postgres, RLS) | **extended.** This is the to-do store. Additive columns, no new table for the to-do itself. |
| `goals` (FG-04/09) + GTS graph (FG-18) | **linked.** A to-do may hang off a goal through the existing `task_goals` edge, so `/graph` shows the same rows without a second ingest. |
| `wa_tasks` / `email_tasks` (pipeline SQLite) | **kept as the pipeline's working state, bridged one-way.** They stay where the MCP tools and the digest read them; a new bridge mirrors the ones that clear the bar into `tasks`. |
| `notifications` (FG-10) | **gets its first production writer.** Nothing in production calls `NotificationStore.create()` today; a to-do that needs the user is exactly what that table is for. |
| `inbound_items` (Incomings, shipped) | **the source anchor.** A triage-born to-do carries the id of the arrival that produced it, so every to-do answers "why am I looking at this?" with a link. |

**No seventh store.** The one rule this plan will not break.

## Scope

**In:** the to-do record and its lifecycle; the triage → to-do bridge with the
noise controls that make it survivable; user notification with C6 quiet-hours
and de-duplication; a `todos` HTTP surface (the first write API for `tasks`);
a `/todos` page in `agent-home` with the two-way links to `/inbox`, `/files`
and `/memory`; "work on this" starting an agent session from the to-do; and
the *seam* for outgoing actions — a completed to-do may propose one, recorded
as an FG-10 approval.

**Out, deliberately:**

- **Actually sending the outgoing action** beyond what already exists. The
  proposal, the approval record and the C4 routing target are in scope; the
  per-channel send implementations are a follow-up FG (§9).
- **A new core model tool.** Footprint ladder rung 2: a `hermes todos …` CLI
  plus a skill file. The agent reaches to-dos the same way it reaches
  incomings.
- **Replacing Kanban or `todo_tool`.**
- **Re-classifying anything.** Which arrivals deserve a to-do is a skill-file
  judgement (Layer 1, hot-reloadable), not Python.
- **Recurring to-dos / reminders.** `cron/` owns recurrence. A cron job may
  *create* a to-do; a to-do does not gain a schedule.

## 1. The record

### Additive columns on `tasks`

```sql
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS stage        TEXT NOT NULL DEFAULT 'open',
        -- staged | open | working | done | dismissed  (see §2)
    ADD COLUMN IF NOT EXISTS priority     TEXT NOT NULL DEFAULT 'normal',
        -- critical | high | normal | low   (mirrors the triage vocabulary)
    ADD COLUMN IF NOT EXISTS due_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS source_kind  TEXT,      -- inbound | analysis | user | agent | cron
    ADD COLUMN IF NOT EXISTS source_ref   UUID,      -- inbound_items.id, when source_kind='inbound'
    ADD COLUMN IF NOT EXISTS source_note  TEXT,      -- one line of provenance for a non-inbound source
    ADD COLUMN IF NOT EXISTS dedupe_key   TEXT,      -- see idempotency, below
    ADD COLUMN IF NOT EXISTS notified_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS snoozed_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS closed_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS outcome      TEXT;      -- free text written on close

CREATE INDEX IF NOT EXISTS tasks_owner_stage_idx
    ON tasks (owner_user_id, stage, priority, created_at DESC);
CREATE INDEX IF NOT EXISTS tasks_source_idx ON tasks (source_kind, source_ref);
CREATE UNIQUE INDEX IF NOT EXISTS tasks_dedupe_idx
    ON tasks (owner_user_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL AND stage IN ('staged', 'open', 'working');
```

Three details that are not cosmetic:

**`origin` gains `'triage'`.** Today it is `explicit | discovered`. A
triage-born to-do is neither: the user did not ask for it, and it did not come
from FG-06's repeated-intent discovery engine. Keeping it separate means the
discovery engine's anti-loop guard and its C6 proposal path stay exactly as
they are, and a `/todos` filter can honestly say where a row came from.

**`normalized_intent` stays NULL for triage to-dos.** `tasks` has
`UNIQUE (owner_user_id, normalized_intent)`, which is FG-06's *one task per
recurring intent* rule. Two unrelated emails that both say "send the invoice"
are two to-dos, and forcing them through that unique key would silently drop
the second. Idempotency for to-dos is the partial index above instead —
scoped to *live* rows, so completing a to-do and receiving the same request
again next month correctly produces a new one.

**`trigger_state` / `completion_state` are NOT NULL and stay that way.** A
light to-do supplies the trivial ladder `('captured' → 'done')`; a to-do that
the user promotes to real work can be given intermediate progress states
through the FG-06 machinery that already exists. The two-state default is what
keeps a to-do cheap without forking the schema.

### Idempotency

`dedupe_key = sha256(owner || source_kind || source_ref || normalize(title))`,
truncated. Triage re-runs, a poller re-reading a UID, a batch replayed after a
crash — all land on the same key and update rather than duplicate. The partial
predicate is the important half: dedupe applies while the to-do is live, never
across its whole history.

### Profile scoping (FG-27)

`tasks` lives in the profile-derived app schema, so a to-do belongs to the
profile whose sub-goal it serves, and the schema owner is resolved
fail-closed exactly as the datastore router now does. Nothing profile-specific
is added here; it is noted because the triage bridge runs *outside* a session
and must therefore resolve its profile the same way
`custom/shared/inbound_registration.py` resolves its owner principal — see §3.

### RLS

`apply_scope_rls` + item grants, identical to `inbound_items`. A to-do quotes
the content of the message that produced it, so it inherits that message's
sensitivity; anything weaker would make `/todos` a way to read another
principal's mail one summary at a time.

## 2. The lifecycle — why "staged" exists

```
        triage / analysis / cron / agent            user, from /todos or chat
                    │                                        │
                    ▼                                        ▼
   ┌─────────┐  above the bar   ┌──────┐   "work on this"  ┌─────────┐
   │ staged  │ ───────────────► │ open │ ────────────────► │ working │
   └─────────┘   notify (§4)    └──────┘                   └─────────┘
        │                          │                            │
        │ auto-expire after N days │ dismiss                    │ complete
        ▼                          ▼                            ▼
   ┌───────────┐              ┌───────────┐              ┌──────┐   may propose
   │ dismissed │              │ dismissed │              │ done │ ─────────────►
   └───────────┘              └───────────┘              └──────┘   outgoing (§9)
```

`staged` is the requirement's word and it earns its place: it is the stage
that **does not notify**. Triage is generous — it extracts an action item from
almost any actionable sentence — and a system that pinged the user for each
one would be abandoned in a week. So the bar for `open` is a judgement made in
a skill file (`todo-decisions.md`, §3), and everything below the bar is
`staged`: present on the page under a "Staged" filter, searchable, promotable
in one tap, silent, and swept away by the expiry job if nobody ever looks.

`status` (FG-06's `pending | in_progress | completed | cancelled`) is kept in
lockstep by the store so the GTS graph and the FG-06 code paths keep working
unchanged; `stage` is the user-facing vocabulary, `status` is the graph's.
They are written together in one transaction, never independently.

Every transition writes a `task_transitions` row with its actor
(`skill:email-triage`, `user:<id>`, `agent:<session>`), which is the audit
trail the FG-06 schema already provides and nothing currently populates for
to-dos.

## 3. Who creates a to-do

### 3.1 Triage (the new requirement)

The four-layer triage architecture makes this a small change in the right
places:

- **Layer 1 (skill, no deploy).** A new `todo-decisions.md` in each of the
  three triage skill directories, stating what deserves a to-do at all, what
  clears the bar into `open`, and what stays `staged`. This is the noise
  control, and it belongs in a hot-reloadable file precisely because the bar
  will be wrong the first week and needs to be tuned on the box in minutes.
- **Layer 2 (schema, no deploy).** `response-schema.md` gains a `todos` array
  beside the existing `tasks` — `{title, why, priority, due_date, stage_hint,
  suggested_action}`. The existing `tasks` field keeps writing
  `wa_tasks`/`email_tasks` unchanged, so the digest and the MCP tools do not
  regress. `todos` is the deliberate, judged subset; the two are not the same
  list and conflating them is how the page fills with noise.
- **Layer 4 (handler, deploy).** `@register('todos')` in
  `custom/shared/triage_handlers.py`, calling a new
  `custom/shared/todo_registration.py`.

`todo_registration.py` is a copy of `inbound_registration.py`'s contract, for
the same reasons: standalone pipeline services never touch the gateway path,
so they resolve the owner principal themselves with the same 60-second retry
guard, and the call is **best-effort and never raises** — a to-do that failed
to register is recoverable by backfill, a message lost because the triage
worker died mid-batch is not.

It needs one thing the file bridge does not: the `inbound_items.id` of the
arrival, so `source_ref` is a real link. The item was registered by
`inbound_registration.register_item()` moments earlier from the same batch, so
the bridge looks it up by the same `(surface, account_id, external_id)` key
rather than threading an id through the triage agent. If the lookup misses
(registration was skipped, Supabase was briefly down), the to-do is still
created with `source_kind='inbound'` and a null `source_ref` plus a
`source_note` — a to-do without its back-link is degraded; a dropped to-do is
a broken promise.

### 3.2 In-depth analysis

Requirement 2's "any in-depth analysis and work will create a to-do entry".
Same call, `source_kind='analysis'`: a skill or background job that decides
something warrants real work records that decision as a to-do instead of
either doing it silently or dropping it. The rule to hold the line on: **if a
code path is about to spend a model call on something the user did not ask
for, it creates a to-do first.**

### 3.3 The user, the agent, and cron

`POST /api/comms/todos` from the page; the same endpoint from the CLI for the
agent (`hermes todos add`, rung 2 of the footprint ladder — no core tool); and
`cron/` jobs that want a human decision rather than an autonomous action.

## 4. Notifying the user

FG-10's `notifications` table is the right home and has been waiting for a
producer. A to-do entering `open` writes one `proactive_ask` row with
`dedupe_key = 'todo:' || tasks.id`, `reversible = TRUE`, and the C6 policy
already applied by `NotificationStore` — quiet-hours hold delivery, the
rate-limit bounds a burst, and answering on one surface settles the other.
`tasks.notified_at` is stamped from the same transaction so the sweep is
idempotent and a to-do is never announced twice.

Three delivery surfaces, in increasing cost:

1. **`/todos` badge + the Inbox Approvals tab** — free, always on.
2. **Telegram push** for `critical`/`high`, through the existing pusher
   pattern (`custom/shared/escalation_pusher_v2.py` already formats and pushes
   from a polled table; a notifications pusher is the same loop against
   Postgres, and is what makes FG-10's table load-bearing rather than
   decorative).
3. **The hourly digest** rolls up everything `open` and un-notified below that
   priority, which is where most to-dos should land.

Batching is the design, not an optimisation: one notification per to-do would
reproduce the escalation-flood problem one layer up.

## 5. The API

The first write surface for `tasks`, mirroring the goals CRUD it sits beside
(`/api/comms/goals*`), in a new `hermes_cli/todos_api.py` router.

| endpoint | purpose |
|---|---|
| `GET /api/comms/todos` | `stage` (csv), `priority`, `source_kind`, `q`, `due_before`, `goal_id`, `tag`, keyset `cursor`, `limit` → `{items, next_cursor}` |
| `GET /api/comms/todos/facets` | counts per stage / priority / source surface — drives the chips, and a chip with no rows is never offered |
| `POST /api/comms/todos` | create (user or agent); `stage` defaults to `open` |
| `GET /api/comms/todos/{id}` | the to-do plus its source arrival, that arrival's attachments, and any linked memory document |
| `PATCH /api/comms/todos/{id}` | title, description, priority, due_at, goal link |
| `POST /api/comms/todos/{id}/promote` | `staged` → `open` (this is what triggers the notification for a staged row) |
| `POST /api/comms/todos/{id}/start` | → `working`; optionally spawns the agent session (§8) |
| `POST /api/comms/todos/{id}/advance` | FG-06 progress-state transition, for a to-do with a real ladder |
| `POST /api/comms/todos/{id}/complete` | → `done`, `outcome`, and optionally a proposed outgoing action (§9) |
| `POST /api/comms/todos/{id}/dismiss` | → `dismissed`, with a reason |
| `POST /api/comms/todos/{id}/snooze` | `snoozed_until`; hidden from the default view until then, then re-notified once |

Keyset pagination from the start, as Incomings settled. Every endpoint
resolves the C1 principal through `_comms_resolve_principal(request,
allow_as=True)` and an `_ensure_table` probe answers an empty page rather than
a 500 on a profile that has never had a to-do.

agent-home BFF routes mirror them one-for-one under `/api/todos/*`, each
forwarding under the bridged principal — a copy of `src/app/api/incomings/`.
Client methods `todos()`, `todo()`, `todoFacets()`, `createTodo()`,
`updateTodo()`, `promoteTodo()`, `startTodo()`, `completeTodo()`,
`dismissTodo()`, `snoozeTodo()` on `HermesApiClient`, with types in
`src/types/index.ts`.

**On the two names.** The store is `tasks` and the surface is *to-dos*. That
is deliberate: renaming the table would break FG-06, FG-18 and every test
around them for a cosmetic gain, while calling the page "Tasks" would put it
in a vocabulary collision with Kanban cards and the agent's scratchpad in the
one place where the user has to understand what they are looking at. The
mapping is stated once, in the router's docstring, and nowhere else.

## 6. The page

`/todos` in `agent-home` (`src/app/todos/page.tsx` + `src/components/todos/`),
server-rendered first page under the resolved principal, exactly as `/inbox`
does. Every component root carries `data-component`.

**Default view:** `open` + `working`, priority then due date then age.

```
◐  Send the signed quote back to Acme                       due Fri  ★ high
   from ✉ ada@acme.com · Email · staged 14:08 · ◇ remembered
   [ Work on this ]  [ Snooze ]  [ Dismiss ]
```

- **Chips:** Open · Staged · Working · Done · All, plus priority and source
  chips built from `/facets`.
- **Filter state in the URL** (`/todos?stage=staged&priority=high`) so a view
  is linkable and survives reload — the Incomings rule.
- **Detail route `/todos/[id]`:** the full description, the provenance block
  (the arrival, quoted, linking to `/inbox/<id>`), its attachments as `/files`
  cards, the linked memory document, the transition history from
  `task_transitions`, and the actions.
- **Two-way links:** `/inbox/[id]` gains "produced 2 to-dos"; `/files` detail
  gains the same when its arrival did.

**Navigation.** The phone bottom bar is budgeted at five and is full. To-dos
belongs in it — it is a daily-decision surface, and a to-do the user does not
see is a to-do that did not happen. `Graph` moves to `SECONDARY_NAV`: the GTS
view is explicitly read-only inspection, valuable but not daily. New primary
bar: Home · To-dos · Chat · Inbox · Memory. The badge on To-dos is the count
of `open` rows, which is also the honest answer to "does this system have
anything for me right now".

## 7. Volume — the failure mode to design against

Triage currently extracts an action item from a large fraction of batches. If
every one became an `open` to-do the page would be unusable within days, the
notifications would be muted, and the feature would be dead. Four controls,
all of them cheap:

1. **The skill bar** (`todo-decisions.md`) — most extractions become `staged`.
2. **Dedupe** on the partial unique index — a thread that says the same thing
   four times is one to-do.
3. **A per-batch cap** (config, default 3) — a 40-message backlog batch cannot
   produce 40 to-dos.
4. **Expiry** — `staged` rows untouched for N days (config, default 14) are
   auto-`dismissed` by the sweep, recorded as a transition with actor
   `system:expiry` so the history says what happened.

The measurement that decides whether the bar is right: the ratio of
`dismissed`-without-being-opened to `done`. It is worth logging from day one.

## 8. Working on a to-do

`POST …/start` moves the row to `working` and, when asked, starts an agent
session seeded with the to-do, its source arrival and its attachments — the
same session-spawn path `cron/` already uses, with the session id recorded on
the transition so `/todos` can link to the trace in `/activity`. The agent
advances progress states through the CLI (`hermes todos advance <id>
<state>`); on completion it writes the outcome. Nothing new is invented here;
this is the FG-06 progress model finally getting a user-visible driver.

Kanban stays out of it. If a to-do turns out to need multi-worker decomposition
that is a Kanban board's job and a human can say so — an automatic bridge from
a personal to-do into a swarm board is a way to spend a lot of tokens on a
misread sentence.

## 9. Outgoing actions — the seam, not the implementation

Requirement 2's last clause. A completed to-do frequently implies something
should leave the system: a reply, a calendar invite, a document sent. The
design position is that **the system may propose; only the user may send**,
and there is already exactly the right primitive for that — an FG-10
`approval` notification carrying a concrete `command`, with `reversible=FALSE`
so C6 can never auto-answer it (D6).

So the seam is:

- `POST …/complete` accepts an optional `proposed_action`
  `{channel, target, subject, body}`;
- it writes an `approval` notification whose body is the drafted message and
  whose target is resolved by `human_comms.resolve_reply_target()` — the C4
  rule that a reply leaves by the same account the message arrived on, which
  already exists and is already tested;
- approving it dispatches through the gateway's existing egress path;
- the decision, either way, is recorded as a transition on the to-do.

What is *not* in this plan: per-channel send implementations beyond what the
gateway already does, drafting quality, or any autonomous send. Those are a
follow-up FG, and this plan is careful to leave the door the right shape
rather than to walk through it.

## Testing

- **Store:** the additive migration is idempotent; an existing FG-06 task row
  reads back unchanged with `stage='open'`; `status` and `stage` never diverge;
  a transition writes its actor.
- **Dedupe:** re-running triage over the same batch produces one to-do; the
  same request after the first is completed produces a second.
- **RLS:** owner reads own; a member cannot read another member's to-dos; an
  elevated owner-role read is labelled — the `tests/hermes_cli` pattern used
  for memories, `file_assets` and `inbound_items`.
- **Bridge:** best-effort — Supabase down during triage logs and the batch
  completes; a missing `inbound_items` row still yields a to-do with a
  `source_note`; the per-batch cap holds.
- **Notification:** an `open` to-do writes exactly one `proactive_ask`;
  quiet-hours holds delivery; `notified_at` makes the sweep idempotent;
  answering on Telegram settles the web row.
- **API:** every endpoint 401s without a principal and 403s across principals;
  keyset paging round-trips; an uninitialised profile gets an empty page, not
  a 500.
- **Frontend:** the default view shows `open`+`working`; URL filter state
  round-trips; the badge counts `open`; `/todos/[id]` renders provenance and
  refuses another principal's row through the BFF; existing `InboxView` and
  nav tests updated for the primary-nav change.
- **Live (systest box):** a real WhatsApp message containing a request
  produces a to-do within one triage cycle, with a working link back to the
  arrival and its attachment; a low-value message produces a `staged` row and
  no notification; completing a to-do with a proposed reply raises an approval
  that is not auto-answered.

## Sequencing

Each step is one PR against `develop`, rebased before push.

1. **Store** — the migration, `stage`/`status` coupling, dedupe, transitions,
   RLS; `hermes_cli/todo_store.py` extending `task_registry.py` (nothing uses
   it yet).
2. **Bridge** — `custom/shared/todo_registration.py`, the `todos` handler, the
   three `todo-decisions.md` + `response-schema.md` edits, the per-batch cap
   and the expiry sweep.
3. **Notification** — `NotificationStore.create()` from step 2's path, the
   notifications pusher, digest roll-up.
4. **API** — `hermes_cli/todos_api.py` + BFF routes + client/types.
5. **Page** — `/todos`, `/todos/[id]`, the nav change, the two-way links from
   `/inbox` and `/files`.
6. **Outgoing seam** — `proposed_action` on complete, the approval record, the
   C4 target resolution.

Steps 1–3 are backend-only and can land first, so that by the time step 5
renders a list there is real, judged data in it — the sequencing that made the
Incomings build work.

## Decisions taken

1. **Extend `tasks`; do not create a seventh store.** The inventory's central
   finding. It costs one migration and buys the GTS graph, the RLS, the
   transitions audit and the goal edges for free.
2. **`staged` is a real stage, not a flag.** The requirement's "all incoming
   will be staged" is a promise that the user is not interrupted for
   everything; the only way to keep it while still capturing generously is to
   make the silent tier first-class.
3. **The bar lives in a skill file, not in Python.** It will be wrong at first
   and must be tunable on the box in minutes, which is exactly what Layer 1 of
   the triage architecture is for.
4. **Notification through FG-10, not a new mechanism.** The table, the C6
   policy and the cross-surface de-duplication already exist and are tested;
   what is missing is a producer, and a to-do is the honest one.
5. **The store keeps the name `tasks`; the surface is called To-dos.**
6. **To-dos take a primary nav slot; Graph moves to secondary.** A decision
   surface used daily outranks a read-only inspection view.
7. **Propose, never auto-send.** Outgoing actions are irreversible approvals
   under D6.

## Open questions for the owner

1. **The bar.** Should the first cut be conservative (only explicit requests
   addressed to you, from known contacts, become `open` — everything else
   `staged`) or generous, tuned down after a week of real volume? Recommend
   conservative: an under-full page invites use, a flooded one gets muted.
2. **Expiry.** 14 days for untouched `staged` rows — too short, too long, or
   never expire and rely on filters?
3. **Nav.** Confirm To-dos takes Graph's bottom-bar slot, or name a different
   one to displace.
4. **Backfill.** Should the existing `wa_tasks` / `email_tasks` history be
   swept into `staged` to-dos on first deploy, or does the feature start
   empty? Recommend: start empty, with `hermes todos backfill --since` kept
   available, because the existing rows were extracted under a bar that did
   not exist yet.
5. **One owner or per-member.** As with the arrival registry, the bridge
   resolves *the* owner principal. Confirm that stays until a second member
   has their own channel.
