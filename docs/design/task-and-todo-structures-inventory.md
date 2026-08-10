# To-do / task structures already in the system — an inventory

**Date:** 2026-08-10 · **Status:** findings, no decision taken · **Origin:** "I need a
to-dos structure in the system. Does the current system have something similar?"

Six exist. None of them is a user-facing to-do list, and the gap is not a
missing data model — it is a missing write API, a missing UI, and a missing
bridge from the tasks the system *already extracts* into the table designed to
hold them.

## 1. `tools/todo_tool.py` — the agent's in-session scratchpad

The `todo` tool the model calls to decompose a long task and keep focus. State
lives on the `AIAgent` instance (one per session) and is re-injected into the
conversation after a context-compression event; bounded at
`MAX_TODO_ITEMS = 256` / `MAX_TODO_CONTENT_CHARS = 4000` precisely because it
rides through compression.

**Not a to-do list for a person.** It is ephemeral, per-session, model-authored,
and disappears when the conversation ends. Statuses (`pending`, `in_progress`,
`completed`, `cancelled`) happen to match the FG-06 registry's, which makes it a
tempting bridge — resist that: promoting scratch plans into durable to-dos would
fill the list with the agent's own bookkeeping.

## 2. Kanban — `hermes_cli/kanban*.py` + `plugins/kanban/dashboard`

A real board: `hermes_cli/kanban_db.py` (SQLite, WAL, append-only `task_events`),
with `kanban_decompose.py`, `kanban_specify.py`, `kanban_swarm.py`, and
`kanban_diagnostics.py` around it. Surfaced by the `/kanban` gateway command and
the `web/` operator dashboard plugin, which tails `task_events` over a WebSocket
for live updates.

**Operator-facing, execution-oriented.** It exists to coordinate multi-worker
swarm execution of development work, not to track "call the accountant". It is
also on the box's local SQLite, so it has no C2 scoping and cannot be
multi-user. Not reachable from `agent-home` at all.

## 3. `hermes_cli/task_registry.py` (FG-06) — the durable, correctly-designed one

Postgres, in the app schema, C2-scoped, with RLS. This is the right home for a
to-do structure and it already exists:

- `tasks` — `owner_user_id`, `visibility`, `title`, `description`,
  `trigger_state` / `completion_state` / `current_state`, `status` ∈
  (`pending`, `in_progress`, `completed`, `cancelled`), `origin` ∈ (`explicit`,
  `discovered`), `UNIQUE (owner_user_id, normalized_intent)`;
- `task_progress_states` — the ordered states one task moves through;
- `task_transitions` — an append-only audit of every state change with its actor;
- `task_discovery_proposals` — the FG-05 signal → C6 consent path by which the
  agent may *propose* a task from repeated intent, with the decision recorded.

Its own docstring is explicit that it is not a second local task DB: session
plans stay in `todo_tool`, swarm execution stays in Kanban, and this is the
app-layer coordination view.

**The problem:** it has no HTTP write surface. `goals` got full CRUD
(`POST /api/comms/goals`, `PUT …/priority`, `POST …/advance`, `POST …/close`);
tasks got nothing. The only way a task row is created today is through
`hermes_cli/goal_management.py` or the GTS code path.

## 4. `hermes_cli/goal_registry.py` (FG-04) + `hermes_cli/goals.py`

Goals with **measurable** criteria: `GoalMetric` computes achievement rather
than letting anyone hand-set "done", and `goals.py`'s Ralph loop is the
per-session execution mechanism for one active goal. Durable, cross-session,
cross-user, C2-scoped. Full CRUD at `/api/comms/goals*`.

**A goal is not a to-do.** It is a prioritised outcome with metrics; a to-do is
a small thing you tick off. They belong in the same graph (see 5) but the same
UI would serve neither well.

## 5. GTS Centre — `hermes_cli/gts.py` (FG-18 / contract C9)

The unified **Goals → Tasks → Skills** graph, and a Core tool under D14/C7:
its rules are immutable to the agent and to users; only a human PR changes
them. It *extends* the two registries above rather than duplicating them
(`parent_goal_id`, `parent_task_id`, `priority`, `score`,
`evaluation_method_ref`, plus `task_goals` / `task_skills` edges and
user-owned-but-agent-immutable `evaluation_methods`). Scores are always
computed, clamped 0–100, and rolled up to parents by priority weight.

Surfaced at `GET /api/gts/graph` and rendered by `agent-home`'s `/graph`
(`components/gts/GtsCentreView.tsx`), which is **explicitly read-only** — its
own docstring says the browser never touches Supabase.

**So the graph can be looked at but not edited from any user surface.**

## 6. `wa_tasks` / `email_tasks` — the tasks the system already extracts, going nowhere

The triage agents do this today: `custom/email/email_triage_agent.py`
"extracts tasks/notes, creates escalations", and the WhatsApp triage agent does
the same. The rows land in the pipeline SQLite
(`/opt/data/whatsapp-messages/whatsapp_data.db`) as `wa_tasks` / `email_tasks`
(`description`, `due_date`, `status`, `priority`, `source_email_id`).

They are reachable only through the MCP tools (`email_list_tasks`,
`whatsapp_list_tasks`) and the hourly Telegram digest. They never reach the
Postgres `tasks` table, so they are invisible to GTS, to `/graph`, and to every
web surface — **the same disconnect as the Incomings problem, one layer up.**

## Adjacent, for completeness

`cron/` (`scheduler.py`, `jobs.py`, `blueprint_catalog.py`) is recurring
scheduled work, not to-dos. `notifications` (FG-10) is the approvals queue —
also a "things awaiting a human" list, but for consent decisions, and nothing
in production ever writes to it (see the Incomings plan, §7).

## The gap, stated plainly

| what exists | what is missing |
|---|---|
| A durable, C2-scoped `tasks` table with progress states and an audit trail | Any way for a user to create, edit, complete or reorder a row in it |
| Full goal CRUD over HTTP | Any task endpoint beyond the read-only `GET /api/gts/graph` |
| Triage that already extracts action items from WhatsApp and email | A bridge from `wa_tasks`/`email_tasks` into `tasks` |
| A read-only `/graph` view of the GTS hierarchy | A to-do surface in `agent-home` — a list you can tick |

Three pieces of work, in dependency order: (1) task CRUD endpoints mirroring
the goals ones; (2) a triage→`tasks` bridge with `source_ref` pointing at the
message that produced the to-do; (3) a to-do surface in `agent-home`. Piece (2)
composes with the Incomings plan (`docs/plans/2026-08-10-001`) — "this email →
this to-do" is one foreign key once both records exist in the same schema.

Not specced here; this document only records what is already built so the next
plan extends it rather than adding a seventh task store.
