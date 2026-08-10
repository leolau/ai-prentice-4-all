# FG-29 — Goal tree + insight promotion (the ai4all spine)

**Wave:** P6-A′ (with FG-24; before FG-26 renders anything) · **Owner agent:** _unassigned_ · **Status:** PLAN — not started

## Summary

ai4all exists to help **one entity** — an individual, a one-person company, a
family, an SME, a school — achieve **one ultimate goal**, with everyone in the
organisation contributing toward it. A **profile** is the instrument built for
one *sub-goal*, carrying the behaviour that sub-goal needs. **People** attach to
the profiles their work spans, supplying real-world input, acting on output, and
contributing know-how back.

That model needs two flows the system does not have:

```
        owner's ultimate goal
                 │  (1) goals flow DOWN — coherence
     ┌───────────┼───────────┐
  finance     product     P5 Chinese      ← profiles = sub-goal instruments
  (CFO)       (CTO)       (teacher)
     └───────────┼───────────┘
                 ▲  (2) insight flows UP — compounding
```

**Down** gives coherence: a sub-goal is only meaningful as a decomposition of
the goal above it, and the agent can only orient a user's request if it knows
both. **Up** gives compounding: what one profile learns must be promotable to
the shared tier, or 500 users produce 500 disconnected conversations instead of
one organisation that gets better.

## Correction to the earlier analysis: goals already exist

An earlier draft of the Phase-6 analysis asserted there was "no goal object
anywhere in Hermes." **That was wrong**, and the correction narrows this FG
considerably. FG-04 and FG-09 shipped a real goal registry in
`hermes_cli/goal_registry.py`, merged and live:

```sql
goals(id, owner_user_id, visibility, title, description,
      priority, status, created_at, updated_at, deadline)
goal_metrics(goal_id, name, target, current, unit,
             source_query, cadence, direction, last_measured_at)
goal_progress(goal_id, metric_name, ts, value, note)
goal_asks(goal_id, user_id, …)      -- proactive measurement solicitation
goal_links(goal_id, resource_kind ∈ {memory,task,tool}, resource_ref)
```

`goals` is the C2-scoped table (RLS applied); metrics, progress and asks hang
off it and are reached through a scoped join. FG-09 unified management across
channels, Telegram, web and MCP.

So this FG does **not** build a goal system. It adds the four things that
registry lacks for the ai4all model:

1. **Hierarchy.** There is no `parent_goal_id` — the registry is a flat,
   prioritised list per profile. An owner goal decomposing into sub-goals cannot
   be expressed.
2. **Reach across profiles.** Goals live in the profile's app schema, and with
   FG-27 each profile gets its own. The owner's goal is invisible to the
   profiles meant to serve it.
3. **Ambient presence.** `goals` is **never injected into the system prompt** —
   confirmed: `agent/system_prompt.py` and `agent/prompt_builder.py` contain no
   goal-loading path. The agent learns a goal only if it *calls a tool*. FG-09
   made that choice deliberately for cache safety ("via tool calls whose results
   are appended — never by mutating the system prompt"). That is right for
   *operational* goals, which change often; it is wrong for a *long-term* goal,
   which is the thing that should orient every turn without being asked for.
4. **Any upward path.** Nothing promotes local knowledge to the shared tier.

## Design / approach

### 1. Hierarchy — one nullable column

```sql
ALTER TABLE goals ADD COLUMN parent_goal_id UUID NULL REFERENCES goals(id);
ALTER TABLE goals ADD COLUMN tier TEXT NOT NULL DEFAULT 'operational';
  -- tier ∈ {'entity', 'profile', 'participant', 'operational'}
```

- **`entity`** — the owner's ultimate goal. Exactly one active per deployment.
- **`profile`** — this profile's sub-goal; its parent is the entity goal.
- **`participant`** — a person's sub-goal *within* a profile (the teacher's
  "P5 Chinese"), parent is the profile goal.
- **`operational`** — today's goals, unchanged, default, no parent required.

Deliberately **no closure table and no arbitrary depth**: three long-lived tiers
plus the existing operational layer covers every example in scope (individual,
OPC, family, SME, school). Depth is capped and validated on write, so the
recursive-CTE and cycle problems that make hierarchies expensive never arise.
If real deployments need deeper nesting, FG-25's closure machinery is the
upgrade path — but it should not be paid for speculatively.

### 2. Down-flow — publish, never a live link

Profiles are independent by design (`AGENTS.md`: a PR adding live config
inheritance from the default profile was closed on exactly this point). So the
entity goal is **copied** into each profile, not read across the boundary:

```
hermes goal publish            # owner-only, audited (C5)
  → for each profile in the registry:
      upsert the entity goal as a local row with
        tier='entity', source_rev=<n>, published_at=<ts>
```

- Each copy records `source_rev`; when the owner edits the entity goal the rev
  bumps and every profile's copy is flagged **stale** until re-published.
  Staleness is visible in `hermes doctor` and in the console.
- A profile can **read** its parent goal and **not write** it. Editing a
  published copy locally is refused, not merged.
- This is the same copy-at-creation shape `--clone` already uses, so it adds no
  new coupling the repo has rejected.

### 3. Ambient presence — a new stable-tier prompt block

The system prompt has three tiers (`agent/system_prompt.py`): `stable`
(identity/SOUL, tool guidance, skills), `context`, and `volatile` (memory
snapshot, USER.md, session line).

Add a **Purpose** block to the **stable** tier, immediately after identity:

```
[PURPOSE]
Ultimate goal (owner):  To improve the learning outcome for 500 primary students
This profile's goal:    To improve P5 Chinese learning outcomes
```

Placing it in `stable` is safe **because these goals are long-lived** — that is
what distinguishes them from FG-04's operational goals, and why FG-09's
"never in the prompt" rule does not apply to them. A long-term goal changing is
a genuine identity change for the agent; invalidating the prefix cache at that
moment is correct, not a regression. The invariant that matters —
**byte-stability for the life of a session** — is preserved: the block is
resolved once at prompt build and frozen, exactly like SOUL.md.

The **participant** goal goes in the **volatile** tier alongside `USER.md`,
where FG-24 already puts per-user content and already freezes a per-session
snapshot. Same tier, same freeze, no new cache surface.

Both are **budget-capped** like memory (a few hundred characters each, refused
above) so a goal cannot grow into an essay that displaces the prompt, and both
run through `_scan_context_content` — goal text is operator-supplied text
entering the system prompt, i.e. an injection surface, and must be scanned on
the same path SOUL.md already uses.

### 4. Up-flow — insight promotion, proposed by a profile, approved by the owner

```sql
insight_candidates(
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_name  TEXT NOT NULL,          -- origin
  proposed_by   TEXT NOT NULL REFERENCES principals(user_id),
  body          TEXT NOT NULL,          -- de-identified, budget-capped
  rationale     TEXT NOT NULL DEFAULT '',
  goal_id       UUID NULL REFERENCES goals(id),   -- what it serves
  status        TEXT NOT NULL DEFAULT 'proposed'  -- proposed|approved|rejected
                CHECK (status IN ('proposed','approved','rejected')),
  reviewed_by   TEXT NULL REFERENCES principals(user_id),
  reviewed_at   TIMESTAMPTZ NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Flow: a profile proposes → the owner reviews in the console → on approval the
body is written to the **shared** memory tier and published down to profiles by
the same mechanism as goals.

Non-negotiable guardrails, because this is the one path that *deliberately*
crosses the isolation boundary:

- **Approval is owner-only and cannot be delegated to a profile admin** — a
  profile admin proposing and approving their own promotion would let one
  cohort write into every other cohort's context.
- **Nothing `private:<user_id>` may be promoted** without that user's explicit,
  recorded consent. The candidate body is authored for promotion, not lifted
  wholesale from someone's memory.
- **Full C5 audit**: proposer, reviewer, origin profile, decision, body hash.
- **Rejected candidates are retained**, so the same idea being proposed five
  times is visible rather than looking like five new ideas.
- The shared tier stays **budget-capped**; approval competes for space, which is
  the forcing function that keeps it signal.

## Reuse map

- `hermes_cli/goal_registry.py` — `goals`/`goal_metrics`/`goal_progress`; add
  two columns, do not fork the table.
- `hermes_cli/goal_management.py` — FG-09's one service layer behind four
  front-ends; the tree extends it rather than adding a parallel path.
- `agent/system_prompt.py` tiers + `load_soul_md`'s scan/truncate path.
- FG-24's per-user snapshot freeze for the participant tier.
- C5 audit, C12 change management, C2 visibility.
- FG-28's profile registry as the publish fan-out list.

## Scope

**In:** `parent_goal_id` + `tier`; entity/profile/participant semantics;
`hermes goal publish` with rev + staleness; the stable-tier Purpose block and
volatile participant block, both capped and scanned; `insight_candidates` with
owner-only approval, promotion to the shared tier and publish-down; console
views for the goal tree and the promotion queue.

**Out:** arbitrary-depth goal trees; automatic sub-goal generation by the agent;
automatic scoring of whether a sub-goal actually serves its parent (a judgement
call, deliberately left to the owner); cross-entity goals.

## Testing requirements

- Tree integrity: a goal cannot be its own ancestor; depth cap enforced on
  write; deleting a parent is refused while children exist.
- **Prompt byte-stability**: the Purpose block is identical across every turn of
  a session; editing the entity goal mid-session does **not** mutate the live
  prompt (it takes effect on the next session).
- Publish: rev bump marks every profile stale; re-publish clears it; a profile
  cannot write its published parent copy.
- Budget: over-cap goal text is refused, not silently truncated into the prompt.
- Injection: goal text goes through the same scanner as SOUL.md — assert a
  planted directive is caught.
- Promotion negative matrix: a profile admin cannot approve; a `private:<user>`
  row cannot be promoted without recorded consent; every decision is audited.
- Real Postgres for anything RLS-adjacent; `goals` is already a C2 table.

## System testing (system-test box)

On `hermes-systest`: set an entity goal, create two profiles with distinct
sub-goals, publish, and confirm each agent's system prompt carries the right
parent+local pair and no other profile's. Propose an insight from one profile,
approve it as owner, and confirm it appears in the other profile only after
publish — and that a profile admin cannot approve it.

## Dependencies

- **Blocked by:** FG-27 Layers 3+1 (per-profile schemas; publish targets them).
- **Related:** FG-04/FG-09 (the registry being extended), FG-24 (participant
  tier shares the snapshot freeze), FG-28 (profile registry = publish fan-out),
  FG-26 (renders the tree and the promotion queue — write this first so the
  console is not retrofitted).
- **Supersedes the need for:** FG-25 in v1. With profiles carrying cohort
  structure, hierarchical multi-dimensional groups are no longer required to
  express departments or classes.

## Definition of Done

`parent_goal_id`/`tier` with validation; publish + staleness; Purpose block in
the stable tier and participant goal in volatile, both capped, scanned and
byte-stable per session; `insight_candidates` with owner-only approval, audit
and publish-down; console goal tree and promotion queue; full test matrix on
real Postgres; `scripts/run_tests.sh`, `ruff`, `ty` clean; system test passed.

## Progress checklist

- [ ] `goals.parent_goal_id` + `tier`, with cycle/depth validation
- [ ] `hermes goal publish` — rev, staleness, read-only parent copies, C5 audit
- [ ] Stable-tier Purpose block (capped, scanned, byte-stable per session)
- [ ] Volatile participant goal block, sharing FG-24's snapshot freeze
- [ ] `insight_candidates` + owner-only approval + promotion to shared tier
- [ ] Console: goal tree view, promotion queue
- [ ] Tests: tree integrity, prompt stability, publish, budget, injection, promotion negative matrix
- [ ] System test on `hermes-systest` passed

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo reframed the domain model: ai4all serves **one entity pursuing one ultimate goal**; a **profile is an instrument for a sub-goal** with matching behavioural characteristics; **people participate in as many profiles as their work spans**, supplying real-world input and contributing know-how back. That reframing resolves the groups-vs-profiles question — a person can already hold a `principals` row in several profiles under one shared GoTrue subject, so multi-cohort membership needs no new machinery and FG-25 is not required for v1 — and it exposes what is actually missing. **Correcting an earlier error in this analysis:** I had asserted Hermes has no goal object; in fact FG-04/FG-09 shipped a full registry (`goals`, `goal_metrics`, `goal_progress`, `goal_asks`, `goal_links`, C2-scoped, four front-ends). What that registry lacks is (1) `parent_goal_id` — it is a flat list, so an owner goal decomposing into sub-goals cannot be expressed; (2) reach across profiles, since goals live in the profile's schema and FG-27 gives each its own; (3) any presence in the system prompt — verified absent from `agent/system_prompt.py` and `agent/prompt_builder.py`, a deliberate FG-09 cache-safety choice that is right for operational goals and wrong for long-term ones; and (4) any upward path for knowledge. Chose publish-with-revision over live inheritance because `AGENTS.md` records a PR closed for coupling profiles, and copy-at-creation is the shape the repo already accepts. Put the Purpose block in the **stable** prompt tier on the argument that a long-term goal is an identity-grade fact like SOUL.md — rare changes legitimately invalidate the prefix cache, and per-session byte-stability, the invariant that actually matters, is preserved. Owner-only approval on insight promotion because it is the one path that intentionally crosses profile isolation: a profile admin approving their own promotion would write into every other cohort's context. | Leo: "a profile is an infrastructure defined to help to improve on sub-goal with similar behavioural characteristics, human-inputs (as users) can assist to provide inputs from the real-world … as well as, to further provide know-hows, insights and innovations on how to achieve the goal." Goals flowing down give coherence; insights flowing up give compounding. Neither existed. |

## Cloud-agent prompt

> Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read
> `docs/design/master-plan/README.md`, `AGENTS.md`, FG-04, FG-09, FG-24, FG-27
> and this doc. **Do not build a goal system — one exists.**
> `hermes_cli/goal_registry.py` already has `goals`/`goal_metrics`/
> `goal_progress`/`goal_asks` and `hermes_cli/goal_management.py` is the single
> service behind four front-ends. Extend them.
>
> **(1) Hierarchy:** add `parent_goal_id UUID NULL REFERENCES goals(id)` and
> `tier TEXT` (`entity`/`profile`/`participant`/`operational`, default
> `operational` so existing rows are untouched). Validate on write: no cycles,
> capped depth. No closure table.
>
> **(2) Down-flow:** `hermes goal publish` copies the entity goal into every
> profile in the registry as a **read-only local row** with `source_rev`;
> editing the entity goal bumps the rev and marks every copy stale, surfaced in
> `hermes doctor`. Copy, never a live cross-profile read — `AGENTS.md` records a
> closed PR on exactly that coupling.
>
> **(3) Ambient presence:** add a `[PURPOSE]` block to the **stable** tier of
> `agent/system_prompt.py`, right after identity, carrying the published entity
> goal and this profile's goal; put the participant goal in the **volatile**
> tier beside `USER.md`, reusing FG-24's per-session snapshot freeze. Budget-cap
> both and run them through the same `_scan_context_content` path SOUL.md uses —
> this is operator text entering the system prompt. The test that matters:
> the block is byte-identical across every turn of a session, and editing a goal
> mid-session does not mutate the live prompt.
>
> **(4) Up-flow:** `insight_candidates` (schema in this doc). A profile
> proposes; **only the owner approves** — a profile admin must not be able to,
> since approval writes into every other profile's context. No `private:<user>`
> content may be promoted without recorded consent. Approved bodies go to the
> shared memory tier and publish down. Full C5 audit including origin profile
> and body hash; keep rejected candidates.
>
> Tests on real Postgres: tree integrity, prompt byte-stability, publish and
> staleness, budget refusal, injection scanning, and the promotion negative
> matrix. Then the `hermes-systest` procedure in this doc.
> `scripts/run_tests.sh`, `ruff`, `ty` clean.
