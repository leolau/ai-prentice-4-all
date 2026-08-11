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
                 CHECK (status IN ('proposed','profile_approved','approved','rejected')),
  profile_reviewed_by TEXT NULL REFERENCES principals(user_id),   -- stage 1: origin profile
  profile_reviewed_at TIMESTAMPTZ NULL,
  owner_reviewed_by   TEXT NULL REFERENCES principals(user_id),   -- stage 2: owner
  owner_reviewed_at   TIMESTAMPTZ NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Flow: a profile proposes a skill it learned → **the origin profile's reviewer**
approves → **the owner** approves → it is copied to `skills-shared/` with
provenance (origin profile, proposer, both approvers, hash) → every profile
picks it up through the external-dirs path it already reads.

**Review is a weekly digest, not an interrupt.** Promotion runs on a batch
cadence: one message listing the skills the profiles learned, each with its
text, its origin and the goal it serves, to approve/reject/edit in one sitting.
That suits the loop's shape — `background_review` already runs asynchronously
after turns and writes *locally*; only the crossing needs a human — and at
weekly cadence human approval costs about a minute, which is why there is **no
auto-approve path even for single-principal deployments**: one code path,
always audited.

**Two stages, because the two reviewers know different things.** The origin
profile's reviewer (the teacher, the CFO) is the only one who can tell whether a
skill carries traces of the people it was learned from; the owner is the only
one who can tell whether it belongs to the whole entity. `principals` carries a
profile-level `reviewer` role; the owner is always the second stage.

Guardrails, because this is the one path that *deliberately* crosses profile
isolation:

- **Owner approval is always the final stage.** A profile admin approving their
  own promotion would write into every other profile's context.
- **The origin profile's reviewer approves first.** In a school this is the
  teacher: a skill distilled from a class's sessions can carry traces of the
  students in it, and only the teacher can see that. This stage is mandatory,
  not advisory.
- **The reviewed artefact is the text.** Approval is review of the SKILL.md
  bytes and `body_hash` pins exactly what was approved, so an edit after
  approval is a new proposal rather than a silent substitution. A skill that
  names individuals or quotes their material must be rejected or rewritten.
- **Nothing `private:<user_id>` is promoted** without recorded consent.
- **Full C5 audit** — origin profile, proposer, approver, hash, decision.
- **Rejections are retained**, so the same idea proposed five times looks like
  repetition rather than five new ideas.
- **Publication takes effect at the next session** for the same reason tier
  changes do: skills are listed in the *stable* prompt tier, so a live
  conversation must not gain one mid-flight.

### 7. One quantitative measure per goal — the thing that keeps the tree honest

A goal nobody measures becomes decoration within a month, and every structure
in this FG rests on the owner believing the goals are live. FG-04 already ships
`goal_metrics` (target, current, unit, direction, cadence, `source_query`), so
the mechanism exists — what is missing is that a goal may have many metrics,
none of them canonical, so nothing can compare two goals or roll a child's
progress into its parent.

Add **one designated primary metric per goal**, in the same units-and-direction
shape as today:

```sql
ALTER TABLE goals ADD COLUMN primary_metric TEXT NULL;   -- FK-ish: goal_metrics.name
-- a goal in tier entity/profile/participant SHOULD have one; operational MAY
```

Consequences, all of which fall out of having a single comparable number:

- **Progress rolls up.** A parent's progress is a declared function of its
  children's primary metrics (default: mean of normalised progress), so the
  owner sees one number for the entity goal that is actually derived from work
  rather than typed in.
- **Skill promotion can be scored** (§8) — a skill is credited against the goal
  it served, and "did the metric move" is the evidence.
- **Sibling conflict becomes detectable** (§9) — two sub-goals can only be shown
  to be pulling against each other if both are measured.
- **`source_query` is the automation seam that already exists.** A metric with a
  query updates itself on its cadence; one without needs a human, and
  unmeasured long-lived goals must be surfaced in the weekly digest as *stale*
  rather than silently sitting at zero.

Normalisation is per metric, honouring `direction` (`at_least`/`at_most`), so
"cashflow days ≥ 60" and "bug count ≤ 20" both reduce to a 0–1 progress figure
that can be averaged and compared.

### 8. Quantified skills — a threshold before a human is asked

Approval decides whether a skill *may* cross, not whether it is any good. Fifty
profiles proposing weekly produces a shared tier of hundreds of skills — and
skills are listed in the **stable** prompt tier, so an unbounded org library
becomes a tax on every turn in every profile. Approval alone does not stop this;
it just makes a human the bottleneck for a queue that should never have been
that long.

So a proposal must **earn its way to the digest**:

```
score = f(usage, breadth, outcome, quality, age)
        │
        ├─ usage    times invoked since learned            tools/skill_usage.py (shipped)
        ├─ breadth  distinct sessions / participants        session + principal ids
        ├─ outcome  movement in the primary metric of the   §7
        │           goal the skill was credited against
        ├─ quality  curator signals: not archived, not      curator + skill_provenance
        │           superseded, no repeated failures
        └─ age      minimum dwell time before it can be     avoids promoting a
                    proposed                                 one-off that looked clever
```

Rules:

- **Below threshold → never shown to a human.** The digest lists only proposals
  that cleared it, with the score and its components as the rationale, so review
  is "is this right for the whole entity?" rather than "is this any good?".
- **The shared library is capped.** Promotion is **competitive**: at the cap, a
  new skill must outscore the weakest resident, which is then demoted (retained
  locally in its origin profile, not deleted). A cap is the only thing that
  bounds the stable-tier cost, and competition is what makes the tier improve
  rather than merely grow.
- **Promoted skills keep being scored.** A skill promoted and then never used in
  any profile is demoted at the next digest. Promotion is a lease, not a
  freehold.
- **The threshold and cap are `config.yaml` values**, not env vars, and their
  defaults must be conservative — the failure mode that matters is a shared tier
  nobody trusts because it is full of noise.

### 9. Sibling conflict — surface it immediately, not in the digest

The CFO instrument optimises cashflow; the CTO instrument wants to spend on
quality. Both correctly serve their sub-goals and they contradict. The goal tree
as drawn assumes alignment and has no way to represent tension — yet tension
between siblings is precisely what an owner needs to see, because it is the
decision only they can make.

Detection, cheap because §7 makes goals comparable:

- **Metric antagonism** — two sibling goals whose primary metrics move in
  opposite directions over the same window, repeatedly.
- **Declared contention** — `goal_links` (shipped) already links goals to tools,
  tasks and memory; two goals contending for the same declared resource (budget,
  a person's time, the same deadline) is a link-level check.
- **Stated conflict** — a profile explicitly records that its sub-goal is
  blocked by a sibling's.

Unlike promotion, this is **not** digest material: a conflict discovered a week
late has already cost a week of work pulling in two directions. It notifies the
owner **immediately**, through the same channel as other owner alerts, with both
goals, the evidence, and the window.

What the system must not do is *resolve* it. The correct output is "these two
are pulling against each other, here is the evidence"; deciding which sub-goal
yields is the owner's judgement, and a system that silently reprioritised one
would be making the entity's strategy on its own. Recording the owner's decision
against both goals is in scope; inferring it is not.

## Reuse map

- `hermes_cli/goal_registry.py` — add two columns; do not fork the tables.
- `hermes_cli/goal_management.py` — FG-09's one service, four front-ends.
- `agent/system_prompt.py` stable/volatile tiers; `load_soul_md`'s scan+cap path.
- `agent/background_review.py` + curator + `skill_provenance.py` — the up-flow's
  producer; FG-29 adds no distillation logic.
- `agent/skill_utils.get_all_skills_dirs` / `is_external_skill_path` and
  `skills.external_dirs` — the shared library seam, already read-only to
  autonomous maintenance.
- `goal_metrics` (`target`/`current`/`unit`/`direction`/`cadence`/`source_query`)
  and `goal_links` — already shipped by FG-04; §7–§9 add a designated primary
  metric and read the links, not a second metrics system.
- `tools/skill_usage.py` + `tools/skill_provenance.py` + curator archive/supersede
  signals — the inputs to the promotion score; no new instrumentation.
- FG-24's per-user snapshot freeze; C5 audit; C12 change management; C2.

## Scope

**In:** `parent_goal_id` + `tier` with lifetime enforcement and
next-session-only tier changes; the operational→entity ladder with parent
declaration; `hermes goal publish` with rev/staleness; the stable-tier Purpose
block and volatile participant block, capped and scanned; the person-level vs
participation-level memory split (amending FG-24); `skills-shared/` as an
external dir plus `skill_promotions` with owner approval and provenance; console
views for the goal tree and the promotion queue; **one designated primary metric
per goal with roll-up**; **a promotion score with a threshold and a capped,
competitive shared library**; **immediate sibling-conflict alerts**.

**Out:** arbitrary-depth goal trees; automatic sub-goal generation; automatic
*resolution* of a sibling conflict (detect and notify only — deciding which
sub-goal yields is the owner's judgement, and a system that reprioritised
silently would be setting the entity's strategy); new metric collection
machinery beyond `source_query`; cross-entity goals; changing how skills are
distilled.

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
- Primary metric: normalisation honours `direction`; parent roll-up matches the
  declared function; a long-lived goal with no metric is reported stale in the
  digest rather than shown as 0%.
- Promotion score: a below-threshold proposal never reaches the digest; at the
  cap, a new skill displaces only a strictly weaker resident and the demoted one
  survives locally in its origin profile; a promoted-but-unused skill is demoted
  at the next digest.
- Conflict: antagonistic sibling metrics raise an alert immediately (not on the
  digest); no automatic reprioritisation occurs; the owner's recorded decision
  attaches to both goals.
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

- [x] `goals.parent_goal_id` + `tier`, cycle/depth validation, `operational` default
- [x] Lifetime enforcement: only long-lived tiers reach a prompt; tier changes apply next session
- [x] Parent declaration on short-lived goals; orphan defaults to the profile goal
- [x] `hermes goal publish` — rev, staleness, read-only copies, C5 audit
- [x] Stable-tier Purpose block (capped, scanned, byte-stable per session)
- [x] Volatile participant goal block, frozen in the same session snapshot
- [ ] Person-level `USER.md` vs participation-level memory split (FG-24 amendment) — **left to FG-24**
- [x] `skills-shared/` wired as an external dir in every profile
- [x] `skill_promotions` + two-stage approval (profile reviewer → owner) on a weekly digest + provenance
- [x] `goals.primary_metric` + direction-aware normalisation + parent roll-up
- [x] Stale-metric reporting for unmeasured long-lived goals
- [x] Promotion score (usage/outcome/age) + `config.yaml` threshold
- [x] Capped, competitive shared library with demotion (local copy retained)
- [x] Sibling-conflict detection + immediate owner alert; no auto-resolution
- [x] Owner surface: entity goal in agent-home settings; goal tree, digest and conflicts on the CLI
- [x] Tests: lifetime matrix, ladder, publish, budget, injection, promotion negatives (real Postgres)
- [ ] System test on `hermes-systest` passed

## Implementation notes (edition 2 → shipped)

Where the implementation departs from the text above, and why:

1. **The memory split is not in this change.** FG-24 is being implemented in
   parallel from the same base; touching per-principal memory here would have
   collided with it. The FG-24 amendment stands — it is simply FG-24's to land.
2. **Placement is enforced twice, not once.** The spec asks for enforcement at
   prompt-build time. The snapshot *writer* refuses a goal whose tier does not
   match the block it is being written into, and the *reader* filters again, so
   a hand-edited `.purpose_snapshot.json` cannot put an operational goal into a
   prompt either.
3. **The prompt reads a per-session snapshot file, not the database.** That is
   what makes the block byte-stable for the life of a conversation, keeps a
   goal edit from mutating a live prompt, and keeps prompt assembly free of
   per-turn work proportional to goals or profiles. `hermes goal sync` (and
   every write path that changes prompt-visible text) rewrites it for the
   *next* session.
4. **Provenance lives in a dedicated `goal_publish_audit` table**, not only in
   progress notes, and staleness propagation walks the profiles that actually
   received a copy (from that audit) rather than every profile on the box.
5. **`skills-shared/` is registered in `config.yaml` on first promotion**
   rather than seeded into every profile at install time — an empty external
   dir in every profile's config was surface with no consumer.
6. **Console:** the entity goal is editable in agent-home settings, as the spec
   requires. The goal tree, the weekly digest, the promotion queue and the
   conflict decisions are CLI-only for now; FG-26 owns those console views and
   this change deliberately does not pre-empt it.
7. **Every threshold is an uncalibrated guess**, labelled as such in
   `config.yaml` and at each definition: the promotion threshold, the shared
   cap, the unused-demotion age, the usage saturation point, the
   unmeasured-goal age, and the number of opposed observations that make a
   conflict. A ~30-user pilot is expected to calibrate them.

## Open questions — all three resolved (2026-08-10)

1. **OPC routing → both, in sequence.** Each profile gets its own bot/channel
   (clear for the human, and no inference means no wrong-brain writes), but a
   deployment starts with one or two profiles and grows as the system *suggests*
   more. That answer was large enough to become its own FG — see **FG-30**.
2. **Auto-approve in single-principal deployments → no.** Promotion is a weekly
   digest, so human approval costs about a minute; one code path, always
   audited, is worth more than the saved minute.
3. **School privacy → the teacher reviews first.** Two-stage approval, origin
   profile reviewer then owner (§6).

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo reframed the domain model: ai4all serves **one entity pursuing one ultimate goal**; a **profile is an instrument for a sub-goal**; **people participate in as many profiles as their work spans**. That resolved the groups-vs-profiles question (a person can already hold a `principals` row in several profiles under one shared GoTrue subject, so FG-25 is not needed for v1) and exposed what was missing. Corrected an error: I had asserted Hermes has no goal object, when FG-04/FG-09 shipped a full registry; what it lacks is `parent_goal_id`, cross-profile reach, prompt presence (verified absent) and an upward path. Chose publish-with-revision over live inheritance per the closed-PR precedent in `AGENTS.md`. | Leo: "a profile is an infrastructure defined to help to improve on sub-goal with similar behavioural characteristics … to further provide know-hows, insights and innovations on how to achieve the goal." |
| 2026-08-10 | 2 | devin (for Leo) | Goal **lifetime** made load-bearing; up-flow rebuilt on the **existing skills loop**; participation + memory split added | Three corrections from Leo. **(1) Goals differ by lifetime** — some last years, some come and go inside a single session — and the system must not conflate them. Lifetime is now a *commitment about mutability* that decides placement: only `entity`/`profile`/`participant` may enter a prompt, `operational` stays tool-appended exactly as FG-09 has it, and a tier change takes effect **at the next session** because the live prompt is frozen for the session's life. That preserves FG-09's rule where it is right instead of overriding it, and it is what makes the stable-tier Purpose block defensible. The ladder deliberately spans both lifetimes — a short-lived goal declares its parent — so the agent can always resolve *which long-term goal does this serve*, and can notice when the answer is "none". **(2) The up-flow already exists**: edition 1 proposed a free-text `insight_candidates` table, which reinvented the self-improvement loop that is Leo's reason for building on Hermes at all. Rebuilt on it — `agent/background_review.py` already distils skills, so the missing piece is only *crossing the profile boundary*. The shared library needs no new mechanism either: `skills.external_dirs` already exists and is **read-only to autonomous curation** (`is_external_skill_path`), which is precisely the property promotion requires — a profile's curator cannot write into the org tier by accident, so the audited promotion path is the only way in. Promoting *skills* rather than prose also means the shared tier accumulates executable, tested artefacts. **(3) Both organisational shapes are one mechanism** — participation = (person × profile) covers many-people-one-sub-goal (SME/school/family) and one-person-many-sub-goals (OPC) without branching. But the OPC case exposed a flaw in FG-24: putting *all* per-user memory inside the profile would duplicate the founder's identity facts across four profiles and let them drift, so memory now splits by what the fact is *about* — person-level `USER.md` shared across a person's participations, participation-level memory isolated per profile. | Leo: "some goals are very long term and don't change very often but some goals are very short-lived and will come and go in every session or even in the middle of a session. The system must be careful with this distinction." · "The existing Hermes infrastructure already support self-improving … this should be the 'Insight flows up'." · "in a One-Person-Company (OPC) the CEO is also the CTO is also the CMO is also the CFO. The same person will provide the real-world connections and insights and knowhows for different sub-goals." |
| 2026-08-10 | 3 | devin (for Leo) | Promotion becomes a **weekly digest with two-stage approval**; auto-approve dropped; open questions closed; profile lifecycle split out to FG-30 | Leo answered all three open questions. **(1)** Promotion cadence is weekly, so human approval is affordable — which removes the reason for the single-principal auto-approve path I had recommended, and one always-audited code path is worth more than a saved minute. Batching also fits the loop's shape: `background_review` already runs asynchronously and writes locally, so only the *crossing* needs a human, and it can wait for a review moment. **(2)** The teacher must review before promotion, which is stronger than the owner-only gate I had and splits the judgement correctly: the origin profile's reviewer is the only person who can tell whether a skill carries traces of the people it was learned from, while the owner is the only person who can tell whether it belongs to the whole entity. Both stages are recorded separately in `skill_promotions`, and because `body_hash` pins the approved bytes, an edit after approval is a new proposal rather than a silent substitution. **(3)** The routing answer — per-profile channels, but starting from one or two profiles with the system suggesting more over time — turned out to be a new capability rather than a UX preference, since every doc so far assumed static profile structure; it is written up as **FG-30 (profile lifecycle: suggest, adopt, retire)** and depends on this FG's digest and promotion path. | Leo: "How often does the skill promotion happen, if once a day or once a week, it is ok to let the human to approve the system suggested promotion" · "Yes, teacher needs to review before promotion" · "Each profile should have its own bot/channel … However, at the beginning, the human may not know what kind of profile does he/she needs." |
| 2026-08-11 | 5 | devin (for Leo) | Implemented (see *Implementation notes*) | Deviations recorded there: the FG-24 memory split stays with FG-24; placement is enforced in both the snapshot writer and reader; the prompt reads a per-session snapshot rather than the registry; publish provenance gets its own audit table; `skills-shared/` is registered on first promotion; tree/digest/conflict views stay on the CLI until FG-26. |
| 2026-08-10 | 4 | devin (for Leo) | Goals get **one comparable measure**; promotion gets a **score + threshold + capped competitive library**; **sibling conflict** alerts immediately | Leo's answers to three holes I raised. **(1) Quantify skills and only surface above-threshold candidates.** Approval was deciding whether a skill *may* cross, not whether it is any good — and since skills are listed in the **stable** prompt tier, an unbounded shared library is a growing tax on every turn in every profile, with a human as the bottleneck for a queue that should never have been that long. Scoring uses only signals already recorded (`tools/skill_usage.py`, curator/provenance, metric movement, dwell time), so this adds arithmetic rather than instrumentation. The cap plus **competitive** promotion is the part that matters most: without it the tier grows monotonically, and with it the tier *improves* — a new skill must outscore the weakest resident, which is demoted but kept locally, and promotion becomes a lease rather than a freehold. **(2) Notify immediately on sub-goal conflict.** The tree as drawn assumed alignment; tension between siblings (CFO's cashflow vs CTO's spend on quality) is exactly what the owner needs and nothing represented it. Deliberately *detect and notify only* — a system that silently reprioritised one sub-goal would be setting the entity's strategy on its own. Immediate rather than digest, because a conflict found a week late has already cost a week of work pulling in two directions. **(3) One shared quantitative measure per goal** turned out to be the enabler for both: `goal_metrics` shipped with FG-04 but no metric is canonical, so nothing could compare two goals, roll a child's progress into its parent, score whether a skill helped, or notice antagonism. A designated `primary_metric` with direction-aware normalisation supplies all four, and `source_query` is the existing automation seam — with unmeasured long-lived goals reported **stale** rather than sitting silently at 0%, since a goal nobody measures becomes decoration within a month and the whole structure rests on the owner believing the goals are live. | Leo: "we need a way to quantify the skills and only show up for approval if it exceed the threshold" · "If there is a sub-goal conflicts, need to notify the user immediately" · "Share the same quantitative measure for each goal" |

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
> above): propose → **origin profile reviewer** approves → **owner** approves,
> both reviewing the SKILL.md **bytes** (`body_hash` pins what was approved) →
> copy to `skills-shared/` with provenance. Review is a **weekly digest**, never
> an interrupt; there is no auto-approve path, including for single-principal
> deployments.
> Refuse promotion of `private:<user>`-derived content without recorded consent.
> Newly approved skills take effect **next session** (skills are listed in the
> stable tier). Full C5 audit; keep rejections.
>
> **(6) Measurement, scoring, conflict (§7–§9).** Add
> `goals.primary_metric` naming one existing `goal_metrics` row per goal;
> normalise by `direction` so goals are comparable; roll a parent's progress up
> from its children. Score every promotion candidate from **already-recorded**
> signals (`tools/skill_usage.py`, curator/provenance, movement in the credited
> goal's primary metric, dwell time) and show the digest **only** candidates
> above a `config.yaml` threshold — never an env var. Cap the shared library and
> make promotion competitive: at the cap a new skill must outscore the weakest
> resident, which is demoted but retained locally. Re-score promoted skills each
> digest and demote unused ones. Detect sibling conflict (antagonistic primary
> metrics, contended `goal_links`, stated blockage) and alert the owner
> **immediately, outside the digest** — detect and notify only, never
> reprioritise.
>
> Tests on real Postgres: the lifetime matrix first, then ladder, publish,
> memory split, budget refusal, injection scanning, promotion negatives,
> roll-up/normalisation, threshold+cap+demotion, and conflict-without-resolution. Then
> the `hermes-systest` procedure above. `scripts/run_tests.sh`, `ruff`, `ty`
> clean.