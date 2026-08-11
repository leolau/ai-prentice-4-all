# FG-29 — Goal tree + skill promotion (the ai4all spine)

**Wave:** P6-A′ (with FG-24; before FG-26 renders anything) · **Owner agent:** _unassigned_ · **Status:** PLAN — not started (edition 2)

## Summary

ai4all exists to help **one entity** — an individual, a one-person company, a
family, an SME, a school — achieve **one ultimate goal**, with everyone in it
contributing toward that goal. A **profile is the instrument for one sub-goal**,
carrying the behaviour that sub-goal needs. **People participate** in as many
profiles as their responsibilities span, supplying real-world input, acting on
output, and contributing know-how back.

Two flows make that an organisation rather than a pile of chatbots:

```
        entity goal  ("improve learning outcomes for 500 primary students")
                 │  (1) goals flow DOWN  — coherence
     ┌───────────┼───────────┐
  P3 Maths    P5 Chinese   Admin          ← profiles = sub-goal instruments
     └───────────┼───────────┘
                 ▲  (2) skills flow UP   — compounding
```

Neither exists today. This FG adds both — and, importantly, **builds each on
machinery Hermes already has** rather than beside it:

| flow | existing machinery | what FG-29 adds |
|---|---|---|
| goals down | FG-04/FG-09 goal registry (`goals`, `goal_metrics`, `goal_progress`, four front-ends) | hierarchy, a **lifetime discipline**, cross-profile publish, prompt placement |
| skills up | the self-improvement loop: `agent/background_review.py` forks after a turn and writes skills; the curator manages their lifecycle | a **shared skill library** and a promotion path across the profile boundary |

## Two corrections that shrank this FG

**Goals already exist.** An earlier draft of this analysis claimed Hermes has no
goal object. Wrong: `hermes_cli/goal_registry.py` ships `goals` (C2-scoped,
RLS), `goal_metrics`, `goal_progress`, `goal_asks`, `goal_links`, and
`hermes_cli/goal_management.py` is one service behind channels, Telegram, web
and MCP. What it lacks is `parent_goal_id`, reach across profiles, and any
presence in the system prompt.

**The up-flow already exists too — as skills.** A previous edition of this doc
proposed a free-text `insight_candidates` table. That was reinventing the
feature that is the reason to build on Hermes at all: the agent already studies
its own sessions and distils reusable skills. What is missing is not *producing*
know-how but **moving it across a profile boundary**. So promotion operates on
**skills**, not prose — a skill is already an executable, tested, formatted
artefact, so the shared tier accumulates things that *work* instead of things
someone found interesting.

## Design / approach

### 1. Goal lifetime is the load-bearing distinction

Some goals last years; some are born and die inside one session. Conflating them
is the single most dangerous thing this FG could do, because **a goal that can
change mid-session must never touch the system prompt** — that is what protects
the per-conversation prefix cache.

So lifetime is not a label, it is a **commitment about mutability**, and it
decides placement:

| tier | lifetime | example | prompt placement |
|---|---|---|---|
| `entity` | years | "Improve learning outcomes for 500 primary students" | **stable** tier |
| `profile` | quarters–years | "Improve P5 Chinese learning outcomes" | **stable** tier |
| `participant` | months–years | "Raise P5 comprehension scores for my 100 students" | **volatile** tier (FG-24 snapshot) |
| `operational` | a session or minutes | "Draft this week's homework set" | **never in the prompt** — tool-appended, exactly as FG-09 does today |

```sql
ALTER TABLE goals ADD COLUMN parent_goal_id UUID NULL REFERENCES goals(id);
ALTER TABLE goals ADD COLUMN tier TEXT NOT NULL DEFAULT 'operational';
  -- CHECK (tier IN ('entity','profile','participant','operational'))
```

Rules the code enforces, not conventions:

- **`operational` is the default**, so every goal that exists today keeps its
  current behaviour and stays out of the prompt.
- **A tier change takes effect at the next session, never mid-conversation.**
  Promoting a short-lived goal to `participant` is legitimate — it is how a
  recurring concern becomes a standing one — but the live prompt is frozen for
  the life of the session, so the promotion is recorded now and rendered later.
- **Only the three long-lived tiers may enter a prompt**, checked at build time
  rather than trusted at write time.
- **Exactly one active `entity` goal** per deployment.

### 2. The ladder spans both lifetimes

This is what makes down-flow real rather than decorative. A short-lived goal
**declares its parent**, so the chain always resolves upward:

```
"draft this week's P5 homework"   operational
   └─ parent: "raise P5 comprehension scores"    participant
        └─ parent: "improve P5 Chinese outcomes" profile
             └─ parent: "improve learning outcomes for 500 students"  entity
```

An operational goal with no declared parent defaults to the profile goal. The
agent can therefore always answer *which longer-term goal does this serve?* —
and, more usefully, notice when the honest answer is "none", which is the signal
the owner actually wants.

Depth stays capped at these four tiers with a self-FK and no closure table:
validated on write, no recursive CTEs, no cycle class of bug. FG-25's closure
machinery remains the upgrade path if a real deployment ever needs deeper
nesting; it should not be paid for speculatively.

### 3. Down-flow — publish, never a live link

Profiles are independent by design (`AGENTS.md` records a closed PR that added
live config inheritance from the default profile: "coupling profiles together is
exactly what the design prevents"). So the entity goal is **copied**:

```
hermes goal publish          # owner-only, audited (C5)
  → upsert into each profile as a local row, tier='entity',
    source_rev=<n>, published_at=<ts>, read-only locally
```

Editing the entity goal bumps the rev and marks every copy **stale**, surfaced
in `hermes doctor` and the console. A profile may read its parent and may not
write it. This is the copy-at-creation shape `--clone` already uses.

### 4. Ambient presence — a stable-tier Purpose block

`agent/system_prompt.py` has three tiers: `stable` (identity/SOUL, tool
guidance, skills), `context`, `volatile` (memory snapshot, USER.md, session
line). Add to **stable**, immediately after identity:

```
[PURPOSE]
Entity goal:   To improve the learning outcome for 500 primary students
This profile:  To improve P5 Chinese learning outcomes
```

Placing it in `stable` is safe **precisely because of the lifetime discipline in
§1**. FG-09's "goals never touch the system prompt" rule is correct for
operational goals and is preserved for them unchanged; the long-lived tiers are
identity-grade facts like `SOUL.md`, where a change legitimately invalidates the
prefix cache the same way editing SOUL does. The invariant that must hold is
**per-session byte-stability**, and it is the first thing to test.

The `participant` goal goes in the **volatile** tier beside `USER.md`, reusing
FG-24's existing per-session snapshot freeze — same tier, same freeze, no new
cache surface.

Both are **budget-capped** (refused above the cap, never silently truncated) and
both run through `_scan_context_content`: goal text is operator-supplied text
entering the system prompt, i.e. an injection surface, and must be scanned on
the same path SOUL.md already uses.

### 5. Participation — one mechanism for both organisational shapes

The unit is **participation = (person × profile)**, and it covers both cases
without branching:

- **Many people, one sub-goal each** (SME, school, family): a human CTO in
  `product`, a teacher in `P5-chinese`, another in `P3-maths`.
- **One person, many sub-goals** (OPC: the founder is CEO, CTO, CMO and CFO):
  the same GoTrue subject holds a `principals` row in four profiles, with four
  working contexts.

Nothing new is needed for either — one shared GoTrue means one subject that each
profile's `principals` row interprets locally.

**But the OPC case exposes a flaw in FG-24 as written.** FG-24 puts *all*
per-user memory inside the profile, so the founder's identity facts — who they
are, how they work, their constraints — would be duplicated four times and drift
apart. Split by what the fact is *about*:

| level | file | scope | example |
|---|---|---|---|
| person | `USER.md` | shared across that person's participations | "prefers concise answers; based in Hong Kong; two children" |
| participation | `memories/users/<id>/MEMORY.md` | one profile only | "the Q3 cashflow model lives in …" (finance profile only) |

One person, one profile-of-self, N working memories. This amends FG-24 and is
recorded there.

### 6. Up-flow — skill promotion over the existing self-improvement loop

**What exists:** `agent/background_review.py` forks the agent after a turn,
replays the conversation, and asks "should any skill/memory be saved or
updated?", writing through a tool whitelist limited to memory and skill
management. The curator handles lifecycle (archive, restore, provenance,
usage). Skills are discovered from `~/.hermes/skills/` **plus configured
`skills.external_dirs`**, which are explicitly *externally owned*: discoverable
and viewable, but **autonomous lifecycle maintenance must treat them as
read-only** (`agent/skill_utils.is_external_skill_path`).

**That last property is the shared library, already built.** The org skill tier
is a directory at the shared root, added to every profile's
`skills.external_dirs`:

```
<hermes-root>/skills-shared/          ← org tier: readable by every profile,
                                        NOT writable by any profile's curator
~/.hermes/profiles/<p>/skills/        ← the instrument's own learned skills
```

Read-only-to-curators is exactly the property promotion needs: a profile's
self-improvement loop cannot write into the shared tier by accident, so the only
way in is the deliberate, audited promotion path.

```sql
skill_promotions(
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  skill_name     TEXT NOT NULL,
  origin_profile TEXT NOT NULL,
  body_hash      TEXT NOT NULL,        -- the reviewed bytes
  proposed_by    TEXT NOT NULL REFERENCES principals(user_id),
  rationale      TEXT NOT NULL DEFAULT '',
  goal_id        UUID NULL REFERENCES goals(id),   -- which goal it serves
  status         TEXT NOT NULL DEFAULT 'proposed'
                 CHECK (status IN ('proposed','approved','rejected')),
  reviewed_by    TEXT NULL REFERENCES principals(user_id),
  reviewed_at    TIMESTAMPTZ NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Flow: a profile proposes a skill it learned → the owner reviews the actual
SKILL.md text → on approval it is copied to `skills-shared/` with provenance
(origin profile, proposer, approver, hash) → every profile picks it up through
the external-dirs path it already reads.

Guardrails, because this is the one path that *deliberately* crosses profile
isolation:

- **Owner-only approval.** A profile admin approving their own promotion would
  write into every other profile's context.
- **Single-principal deployments auto-approve.** In an OPC the owner is the only
  principal, so requiring them to approve their own proposal is pure friction;
  the promotion is still recorded and audited, just not gated. *(Open question 2
  below — this is my recommendation, not a decision.)*
- **The reviewed artefact is the text.** A skill distilled from one class's
  sessions can carry traces of the students in it, so approval is review of the
  SKILL.md bytes, and `body_hash` pins exactly what was approved. A skill that
  names individuals or quotes their material must be rejected or rewritten.
- **Nothing `private:<user_id>` is promoted** without recorded consent.
- **Full C5 audit** — origin profile, proposer, approver, hash, decision.
- **Rejections are retained**, so the same idea proposed five times looks like
  repetition rather than five new ideas.
- **Publication takes effect at the next session** for the same reason tier
  changes do: skills are listed in the *stable* prompt tier, so a live
  conversation must not gain one mid-flight.

## Reuse map

- `hermes_cli/goal_registry.py` — add two columns; do not fork the tables.
- `hermes_cli/goal_management.py` — FG-09's one service, four front-ends.
- `agent/system_prompt.py` stable/volatile tiers; `load_soul_md`'s scan+cap path.
- `agent/background_review.py` + curator + `skill_provenance.py` — the up-flow's
  producer; FG-29 adds no distillation logic.
- `agent/skill_utils.get_all_skills_dirs` / `is_external_skill_path` and
  `skills.external_dirs` — the shared library seam, already read-only to
  autonomous maintenance.
- FG-24's per-user snapshot freeze; C5 audit; C12 change management; C2.

## Scope

**In:** `parent_goal_id` + `tier` with lifetime enforcement and
next-session-only tier changes; the operational→entity ladder with parent
declaration; `hermes goal publish` with rev/staleness; the stable-tier Purpose
block and volatile participant block, capped and scanned; the person-level vs
participation-level memory split (amending FG-24); `skills-shared/` as an
external dir plus `skill_promotions` with owner approval and provenance; console
views for the goal tree and the promotion queue.

**Out:** arbitrary-depth goal trees; automatic sub-goal generation; automatic
scoring of whether a sub-goal really serves its parent (a judgement call, left
to the owner); cross-entity goals; changing how skills are distilled.

## Testing requirements

- **Lifetime discipline** (the critical matrix): an `operational` goal never
  appears in any prompt tier; a tier change mid-session does not alter the live
  prompt; the Purpose block is byte-identical across every turn of a session.
- Ladder: parent chain resolves operational → entity; an orphan operational goal
  defaults to the profile goal; no cycles; depth cap enforced on write.
- Publish: rev bump marks copies stale; re-publish clears; a profile cannot
  write its published parent copy.
- Memory split: a person in two profiles has one `USER.md` and two separate
  participation memories; a participation fact does not leak to the other.
- Budget refusal and injection scanning on goal text.
- Promotion negative matrix: a profile admin cannot approve; a promoted skill
  cannot be written by a profile's curator into `skills-shared/`; a
  `private:<user>`-derived skill is refused without consent; a newly approved
  skill does not enter a live session's prompt; audit rows complete.
- Real Postgres for anything RLS-adjacent (`goals` is already a C2 table).

## System testing (system-test box)

On `hermes-systest`: set an entity goal; create two profiles with distinct
sub-goals; publish; confirm each agent's prompt carries the right parent+local
pair and no other profile's. Create a session-scoped operational goal under a
participant goal and confirm the chain resolves while the prompt stays
byte-stable. Distil a skill in one profile, propose it, approve as owner, and
confirm the other profile picks it up on its **next** session — and that a
profile admin cannot approve it.

## Dependencies

- **Blocked by:** FG-27 Layers 3+1 (per-profile schemas; publish targets them).
- **Amends:** FG-24 (person-level vs participation-level memory).
- **Related:** FG-04/FG-09 (the registry being extended), FG-28 (profile
  registry = publish fan-out), FG-26 (renders the tree and promotion queue —
  sequence this first so the console is not retrofitted).
- **Supersedes the need for:** FG-25 in v1.

## Definition of Done

Tier + parent with lifetime enforcement and next-session semantics; publish with
staleness; Purpose block stable-tier and participant volatile, capped, scanned,
byte-stable per session; person/participation memory split; `skills-shared/` +
`skill_promotions` with owner approval, provenance and audit; console goal tree
and promotion queue; full matrix on real Postgres; `scripts/run_tests.sh`,
`ruff`, `ty` clean; system test passed.

## Progress checklist

- [ ] `goals.parent_goal_id` + `tier`, cycle/depth validation, `operational` default
- [ ] Lifetime enforcement: only long-lived tiers reach a prompt; tier changes apply next session
- [ ] Parent declaration on short-lived goals; orphan defaults to the profile goal
- [ ] `hermes goal publish` — rev, staleness, read-only copies, C5 audit
- [ ] Stable-tier Purpose block (capped, scanned, byte-stable per session)
- [ ] Volatile participant goal block on FG-24's snapshot freeze
- [ ] Person-level `USER.md` vs participation-level memory split (FG-24 amendment)
- [ ] `skills-shared/` wired as an external dir in every profile
- [ ] `skill_promotions` + owner approval (+ single-principal auto-approve) + provenance
- [ ] Console: goal tree, promotion queue
- [ ] Tests: lifetime matrix, ladder, publish, memory split, budget, injection, promotion negatives
- [ ] System test on `hermes-systest` passed

## Open questions (for the owner)

1. **OPC routing.** When one person runs four sub-goal profiles, how do they
   address the right instrument? One bot/channel per profile (explicit, zero
   routing logic, four chats) or one entry point that infers the sub-goal
   (nicer, but a wrong guess writes context into the wrong brain). Biggest
   remaining UX decision; nothing else in this FG depends on it.
2. **Auto-approve promotion in single-principal deployments?** Recommended
   above; confirm.
3. **School privacy.** Is "the owner reviews the SKILL.md text before it
   crosses" sufficient for skills distilled from student sessions, or should
   promotion from a profile containing minors' data require something stricter?

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo reframed the domain model: ai4all serves **one entity pursuing one ultimate goal**; a **profile is an instrument for a sub-goal**; **people participate in as many profiles as their work spans**. That resolved the groups-vs-profiles question (a person can already hold a `principals` row in several profiles under one shared GoTrue subject, so FG-25 is not needed for v1) and exposed what was missing. Corrected an error: I had asserted Hermes has no goal object, when FG-04/FG-09 shipped a full registry; what it lacks is `parent_goal_id`, cross-profile reach, prompt presence (verified absent) and an upward path. Chose publish-with-revision over live inheritance per the closed-PR precedent in `AGENTS.md`. | Leo: "a profile is an infrastructure defined to help to improve on sub-goal with similar behavioural characteristics … to further provide know-hows, insights and innovations on how to achieve the goal." |
| 2026-08-10 | 2 | devin (for Leo) | Goal **lifetime** made load-bearing; up-flow rebuilt on the **existing skills loop**; participation + memory split added | Three corrections from Leo. **(1) Goals differ by lifetime** — some last years, some come and go inside a single session — and the system must not conflate them. Lifetime is now a *commitment about mutability* that decides placement: only `entity`/`profile`/`participant` may enter a prompt, `operational` stays tool-appended exactly as FG-09 has it, and a tier change takes effect **at the next session** because the live prompt is frozen for the session's life. That preserves FG-09's rule where it is right instead of overriding it, and it is what makes the stable-tier Purpose block defensible. The ladder deliberately spans both lifetimes — a short-lived goal declares its parent — so the agent can always resolve *which long-term goal does this serve*, and can notice when the answer is "none". **(2) The up-flow already exists**: edition 1 proposed a free-text `insight_candidates` table, which reinvented the self-improvement loop that is Leo's reason for building on Hermes at all. Rebuilt on it — `agent/background_review.py` already distils skills, so the missing piece is only *crossing the profile boundary*. The shared library needs no new mechanism either: `skills.external_dirs` already exists and is **read-only to autonomous curation** (`is_external_skill_path`), which is precisely the property promotion requires — a profile's curator cannot write into the org tier by accident, so the audited promotion path is the only way in. Promoting *skills* rather than prose also means the shared tier accumulates executable, tested artefacts. **(3) Both organisational shapes are one mechanism** — participation = (person × profile) covers many-people-one-sub-goal (SME/school/family) and one-person-many-sub-goals (OPC) without branching. But the OPC case exposed a flaw in FG-24: putting *all* per-user memory inside the profile would duplicate the founder's identity facts across four profiles and let them drift, so memory now splits by what the fact is *about* — person-level `USER.md` shared across a person's participations, participation-level memory isolated per profile. | Leo: "some goals are very long term and don't change very often but some goals are very short-lived and will come and go in every session or even in the middle of a session. The system must be careful with this distinction." · "The existing Hermes infrastructure already support self-improving … this should be the 'Insight flows up'." · "in a One-Person-Company (OPC) the CEO is also the CTO is also the CMO is also the CFO. The same person will provide the real-world connections and insights and knowhows for different sub-goals." |

## Cloud-agent prompt

> Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read
> `docs/design/master-plan/README.md`, `AGENTS.md`, FG-04, FG-09, FG-24, FG-27
> and this doc.
>
> **Build nothing that exists.** There is already a goal registry
> (`hermes_cli/goal_registry.py`, one service in `hermes_cli/goal_management.py`
> behind four front-ends) and already a self-improvement loop that distils
> skills (`agent/background_review.py` + curator). Extend both.
>
> **(1) Lifetime discipline — the part to get right.** Add
> `parent_goal_id UUID NULL REFERENCES goals(id)` and `tier`
> (`entity`/`profile`/`participant`/`operational`, default `operational` so
> existing rows are untouched). Enforce at prompt-build time that **only the
> three long-lived tiers can reach a prompt**, and that a **tier change applies
> at the next session, never mid-conversation**. Short-lived goals declare a
> parent; an orphan defaults to the profile goal. No closure table; validate
> cycles and depth on write.
>
> **(2) Down-flow:** `hermes goal publish` copies the entity goal into every
> profile as a **read-only** local row with `source_rev`; editing bumps the rev
> and marks copies stale (surface in `hermes doctor`). Copy, never a live
> cross-profile read — `AGENTS.md` records a closed PR on exactly that coupling.
>
> **(3) Prompt:** `[PURPOSE]` block in the **stable** tier of
> `agent/system_prompt.py` after identity (entity + profile goals); participant
> goal in the **volatile** tier beside `USER.md`, reusing FG-24's snapshot
> freeze. Budget-cap both; run both through `_scan_context_content` — this is
> operator text entering the system prompt. Test that the block is
> byte-identical across every turn and that editing a goal mid-session does not
> mutate the live prompt.
>
> **(4) Memory split (amends FG-24):** person-level `USER.md` shared across a
> person's participations; participation-level memory per profile. Test that one
> person in two profiles has one profile-of-self and two isolated working
> memories.
>
> **(5) Up-flow:** add `<hermes-root>/skills-shared/` to every profile's
> `skills.external_dirs` — it is already read-only to autonomous curation
> (`agent/skill_utils.is_external_skill_path`), which is the property that keeps
> a profile's curator out of the org tier. Add `skill_promotions` (schema
> above): propose → **owner-only** approval of the SKILL.md **bytes**
> (`body_hash` pins what was approved) → copy to `skills-shared/` with
> provenance. Auto-approve only when the deployment has a single principal.
> Refuse promotion of `private:<user>`-derived content without recorded consent.
> Newly approved skills take effect **next session** (skills are listed in the
> stable tier). Full C5 audit; keep rejections.
>
> Tests on real Postgres: the lifetime matrix first, then ladder, publish,
> memory split, budget refusal, injection scanning, promotion negatives. Then
> the `hermes-systest` procedure above. `scripts/run_tests.sh`, `ruff`, `ty`
> clean.
