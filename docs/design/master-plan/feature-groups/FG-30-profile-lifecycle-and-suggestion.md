# FG-30 — Profile lifecycle: suggest, adopt, retire

**Wave:** P6-D (after FG-29 — suggestion is an output of the same loop) · **Owner agent:** _unassigned_ · **Status:** IMPLEMENTED, PARTLY VERIFIED — the suggestion/adopt/retire layer shipped in #250 and its review defects are fixed (edition 4). Not yet: a user-facing surface in `agent-home`, anything that *schedules* the monthly pass, and the live system test on `hermes-systest`.

## Summary

Every other Phase-6 doc assumes someone can name their sub-goals on day one and
create a profile for each. **They can't.** An owner starting ai4all knows their
ultimate goal ("make a great product that sells") and nothing about how the work
will divide. Sub-goal structure is discovered by doing the work, not designed
before it.

So profiles must have a **lifecycle**, and profile creation must be an *output
of the learning loop* rather than a setup step:

```
start with ONE profile
        │  work happens; the loop distils skills and watches where work clusters
        ▼
system SUGGESTS a new profile  ("a lot of your finance work looks unlike the rest")
        │  owner adopts, edits, or dismisses
        ▼
profile ADOPTED — gets a sub-goal, a channel when the owner commits, its own memory
        │
        ▼
profile RETIRED or MERGED when its sub-goal is done or was a bad split
```

This is what makes "one AI system easy to set up and use" true for a person who
cannot yet name their own sub-goals — and it is why the answer to "one channel
per profile or one entry point?" is **both, in sequence**: start with one, grow
into several, each with its own channel once it has earned one.

## Design / approach

### 1. Suggestion rides the loop that already exists

FG-29 established that the up-flow is the shipped self-improvement loop
(`agent/background_review.py` distils skills; the curator manages them). Profile
suggestion is **the same evidence read at a different granularity**: when a
cluster of work has its own vocabulary, its own skills and its own recurring
goals, that cluster is a sub-goal wanting its own instrument.

Signals, all already recorded, none requiring new instrumentation:

| signal | source |
|---|---|
| skills clustering into an unrelated domain | skill names/categories + `tools/skill_usage.py` |
| operational goals repeatedly laddering to the same *implicit* parent | FG-29 `parent_goal_id` (orphans defaulting to the profile goal are the tell) |
| sessions whose topic is far from the profile's own description | `hermes_cli/profile_describer.py` already summarises "what this profile is good at" via the aux LLM |
| distinct participants doing distinct work in one profile | `principals` + per-participation memory (FG-24 amended) |

A suggestion is a proposal with evidence attached, never an automatic creation:

```sql
profile_suggestions(
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  proposed_name    TEXT NOT NULL,       -- the label, role-shaped is allowed (§1.2)
  proposed_role    TEXT NOT NULL,       -- what this profile would *be*
  proposed_goal    TEXT NOT NULL,       -- the sub-goal it would serve — always required
  parent_goal_id   UUID NULL REFERENCES goals(id),
  rationale        TEXT NOT NULL,       -- the evidence, in the owner's language
  evidence         JSONB NOT NULL,      -- skills / goals / session ids behind it
  dedup_key        TEXT NOT NULL,       -- stable key over the evidence; latches a dismissal
  origin_profile   TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'proposed'
                   CHECK (status IN ('proposed','adopted','dismissed')),
  reviewed_by      TEXT NULL REFERENCES principals(user_id),
  reviewed_at      TIMESTAMPTZ NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

**Only the owner adopts** — a new profile is a change to the entity's goal tree.
Dismissals are kept and latch on `dedup_key`, so a dismissed suggestion is never
re-proposed on the same evidence; sprawl by nagging is the failure mode to design
against.

### 1.1 Cadence and volume — Leo's decision, and what it costs

**At most one open suggestion at a time, generated at most once a month.**

Rationale, because a later reader will be tempted to raise it: the costs are
asymmetric. A suggestion held back returns next month; one dismissed unread never
returns, because the dismissal latches on the evidence. Several at once read as a
list, and lists get skimmed and batch-dismissed — which kills the good suggestion
along with the noise, permanently.

That has a structural consequence the earlier edition got wrong. It said
"runs on the same weekly digest", but **FG-29's digest is weekly** and this pass
is monthly, so the two cannot be the same clock:

- **generation** is its own monthly pass with its own last-run timestamp,
  **skipped entirely while a suggestion is still `proposed`** (one open at a time
  is the cap — simpler and stronger than a per-run limit);
- **rendering** still rides `weekly_digest()`
  (`hermes_cli/goal_conflicts.py`, which returns `(title, lines)` and schedules
  nothing), so an open suggestion appears in every weekly review until it is
  reviewed. One review moment for the owner; two clocks underneath.
- the runner-up cluster is **not** emitted and **not** latched — it must be free
  to win next month.

### 1.2 Naming — role and goal, both required

Leo's decision: the suggestion carries **all the necessary information, including
the role and the goal.** So `proposed_role` and `proposed_goal` are both NOT NULL,
and a role-shaped label ("Finance", "CFO") is allowed as the profile name.

Why both, rather than picking one: people think in roles, and in a one-person
company the role *is* the sub-goal — but a role has no end state, and FG-30's
retire path (§4) fires when a sub-goal **completes**. A role-only suggestion could
therefore never retire, producing exactly the sprawl this FG exists to bound. The
label is for the human; `proposed_goal` is what the goal tree hangs on and what
retirement completes.

### 1.3 Relationship to the suggestion surface that already ships

`cron/suggestions.py` **already implements this pattern** — proposals from several
sources (`catalog`, `blueprint`, `usage`, `integration`), consent-first
acceptance, and dismissals latched by a stable `dedup_key` so nothing is
re-offered. A cold reader must not rediscover it and must not build a second
latch: **reuse its contract** (never auto-create, explicit acceptance, latched
dismissal, stable dedup key over the evidence) and mirror its vocabulary.

It is nonetheless a **separate store**, and the reason has to be stated rather
than assumed: accepting a `cron` suggestion calls `cron.jobs.create_job` — that
surface is a proposal layer over *one job engine*, its store is a JSON file at
`~/.hermes/cron/suggestions.json`, and a profile proposal needs `evidence` JSONB
and foreign keys into `goals`/`principals` that a JSON file cannot enforce. So:
same contract, different store, and no second *mechanism* for latching.

If the console ends up rendering both queues, they should read as one list to the
owner even though they are two tables.

### 1.4 Where the suggestion row lives — an FG-27 consequence

`profile_suggestions` references `goals(id)` and `principals(user_id)`, which are
**profile-local** (FG-27: each profile owns its `app_prod[_<profile>]` schema). So
a suggestion *about creating another profile* is stored inside the profile whose
work produced the evidence — `origin_profile` is that profile, not a pointer into
a shared registry.

Consequences an implementer must not have to guess:

- the queue an owner sees is per-profile; showing "every open suggestion" is a
  cross-profile read, which is FG-28's chokepoint, not a join;
- adoption writes the new profile's rows into the **new** schema and must not
  attempt to move the suggestion row there;
- there is no shared-root suggestions table, and adding one would need FG-28's
  registry to own it.

### 2. Adoption creates a *fresh* profile, and deliberately does not migrate history

The tempting design is to "split" a profile: divide its memory between parent
and child. **Reject it.** Deciding which memory card belongs to which half is a
judgement no heuristic makes well, nobody will do it by hand, and a
half-migrated memory is worse than none — the person can no longer trust either
side.

Adoption therefore creates a new profile that inherits only what is
unambiguously transferable:

| inherited | not inherited |
|---|---|
| the sub-goal (+ published entity goal, FG-29 §3) | the parent's session history |
| **promoted** skills from the shared tier | the parent's un-promoted local skills |
| the person's person-level `USER.md` (FG-24 amended) — **already shared, see below** | the parent's participation-level memory |
| model/provider config via the existing `--clone-config` path | the parent's `.env` credentials (FG-27: never inherit a resolved DSN) |

Lossy and honest. And it gives the promotion path a second purpose: **a skill
that was promoted is a skill a new profile starts with**, so the org tier is
what makes each new instrument better than the first one was.

Reuse: `create_profile(name, clone_config=..., description=...)` already exists
and already takes a description; `profile.yaml` already carries
`description` + `description_auto`; `hermes profile describe --auto` already
generates that description from skills+model via the aux LLM. Adoption is
mostly wiring existing calls behind a reviewed proposal.

**Do not implement the `USER.md` inheritance — it already holds, and copying it
would be a regression.** FG-24's amendment (edition 3) put person identity at
`<root>/persons/<user_id>/USER.md`, *outside* any profile home, precisely so
copies cannot drift. A new profile therefore sees the same person-level file with
no work at all. The task here is to **assert** it (and to assert that
participation-level memory does *not* follow), not to copy anything. A cold agent
that reads "inherited" as "copy on adoption" reintroduces the drifting-copies
problem that amendment exists to remove.

### 3. Channels: a suggested profile starts channel-less

Leo's decision is that each profile should have its own bot/channel — clearer
for the human and for the system, and it removes any need for the gateway to
*infer* which sub-goal a message belongs to (a wrong guess writes context into
the wrong brain, which is worse than an extra chat window).

But a bot token requires a human at BotFather. **If profile creation is meant to
be routine and system-suggested, a mandatory credential step is exactly what
will stop people doing it.** So:

- an adopted profile is immediately usable from **`agent-home` and the CLI**, with
  no channel and no credential;
- it gains a channel when the owner commits to it — a deliberate act, at which
  point the credential friction is proportionate;
- `hermes doctor` reports channel-less profiles so they don't get forgotten;
- the gateway already refuses two profiles polling one bot — verified: a fatal
  config error exits `EX_CONFIG` (78) and `service_manager`'s finish script turns
  that into a permanent stop rather than a restart loop, so the "just reuse the
  token" shortcut fails loudly instead of silently interleaving two sub-goals.

This is the honest sequencing of Leo's "support both": one entry point at the
start because there is only one profile, per-profile channels as the structure
earns them.

### 4. Retirement and merge — the part a suggestion mechanism cannot go without

A system that only proposes new profiles produces sprawl, and sprawl is
expensive here: every profile is another memory, another channel and another
thing the person must remember to address.

- **Retire** — the sub-goal is achieved or abandoned. The profile is archived
  (the existing export path), its channel released, its goal marked `completed`.
  Before archiving, the owner is offered its skills for promotion **once**: that
  is the only way its know-how survives, and it is the promotion path pointed
  sideways rather than up.
- **Merge** — the split was wrong. Both profiles' skills go through promotion;
  the loser is archived. Memory is **not** merged, for the §2 reason.
- **Idle detection** — the weekly digest flags profiles with no sessions for N
  weeks rather than waiting for someone to notice.

Archives are kept (`hermes profile export`), so retirement is reversible even
though memory division is not.

### 4.1 Which surface renders all of this

**`agent-home`, per D20** — every user-facing screen in this FG (the suggestion
queue with its evidence, adopt/dismiss, commit-to-channel, retire/merge) lands in
`agent-home/`, not the `web/` dashboard. Earlier editions said "console", which is
ambiguous and, read as the dashboard, would put the main surface of this FG in the
frozen operator console.

One ordering constraint that must not be discovered late: **FG-28 has not shipped a
profile switcher**, and its cross-profile console routing covers only
members/directory/whoami. So an owner in profile A cannot yet review a suggestion
raised in profile B. FG-30 must therefore ship its queue as a **profile-local**
surface (§1.4) and must not block on a switcher — if a cross-profile view is
wanted, it is FG-28 work, not a join to be invented here. This is the FG-26 item-1
trap (an item whose precondition lives two waves later) and it is called out so
nobody re-walks into it.

### 5. The first goal — a default, editable in settings

FG-30 fixes the "which profiles do I need?" cold start, but there is a second
one just before it: a new owner meets an empty system and a goal-shaped hole,
and an entity goal nobody wrote means every downstream mechanism (publication,
roll-up, conflict detection, skill scoring) has nothing to hang off.

So the entity goal is **seeded with a system default** at first run — a generic
but real long-term goal, e.g. *"To optimise the effectiveness of this
organisation"* — marked as a default so the console can prompt for a better one
without nagging. It is then **editable in `agent-home` settings**
(`agent-home/src/app/settings` + `components/settings/SettingsView.tsx`, which
already exist), alongside its primary metric (FG-29 §7).

Two properties this must keep:

- **Seeded, not blank.** A default goal that is obviously generic invites
  replacement; an empty field invites being skipped.
- **Editing it is a publication event.** Changing the entity goal bumps the
  revision and marks every profile's copy stale (FG-29 §3) — the settings page
  is a writer into the goal tree, not a text box.

The existing `agent-home/src/app/onboarding` route is the natural place to ask
for it the first time.

### 6. Invitation delivery — the owner's own channel, by decision

Earlier drafts treated the absence of SMTP as a hole. It is a **decision**: the
owner shares the invitation link by whatever means they already use. That is
honest about the deployment (a family or an OPC has no mail server and does not
want one) and it keeps FG-26 free of a mail dependency.

What it costs must stay written down rather than being forgotten: a link relayed
through a chat app sits in that app's scrollback and on its servers, and the
relaying owner could in principle activate the account themselves. The short TTL
and single-use property (FG-26) bound the window; "the user set their own
password" is not an integrity property this deployment can claim. Recorded here
so the trade is visible if the audit ledger is ever used in a dispute.

## Reuse map

- `hermes_cli/profiles.py` — `create_profile` (already takes `description`,
  `clone_config`), `delete_profile`, export/import, `profile.yaml` meta,
  `list_profiles`.
- `hermes_cli/profile_describer.py` — aux-LLM description of what a profile is
  good at; the same seam names and describes a *suggested* profile.
- `agent/background_review.py` + `tools/skill_usage.py` + curator — the
  evidence.
- **`cron/suggestions.py` — the shipped proposal surface whose contract this FG
  reuses (consent-first, `dedup_key`-latched dismissal); see §1.3 for why the
  store is separate and the mechanism is not duplicated.**
- FG-29 — goal publication, the promotion path, and `weekly_digest()` in
  `hermes_cli/goal_conflicts.py`, which returns `(title, lines)` and schedules
  nothing (so *rendering* rides it; *generation* needs its own monthly clock).
- FG-24 amendment — person identity at `<root>/persons/<user_id>/USER.md`, outside
  the profile home (so §2's inheritance is already true).
- FG-27 — never inherit a resolved DSN; new profile gets its own schema.
- FG-28 — gateway collision detection; **note it has *not* shipped a profile
  switcher**, so the queue is profile-local (§1.4, §4.1).
- `agent-home/src/app/settings` + `components/settings/SettingsView.tsx` and the
  existing `onboarding` route — the first-goal editor, already built.
- FG-26 invitations (delivery is the owner's own channel, by decision).
- C5 audit; C12 change management.

## Scope

**In:** a **seeded default entity goal** editable in `agent-home` settings (an
edit is a publication event); `profile_suggestions` with evidence, role **and**
goal, and owner-only adoption; **monthly** generation, at most one open suggestion
at a time, rendered in FG-29's weekly digest; adoption wiring `create_profile`
with sub-goal + promoted skills (person-level `USER.md` asserted, not copied);
channel-less start and the commit-to-channel step; retire/merge with a one-time
promotion offer; idle detection; the `agent-home` suggestion queue; `hermes
doctor` reporting.

**Out:** automatic profile creation without review; memory splitting or merging
(deliberately — §2); automatic channel provisioning (needs a human at the
platform); cross-entity profile templates; re-proposing a dismissed suggestion
on the same evidence; a second latching mechanism beside `cron/suggestions.py`'s
(§1.3); a cross-profile suggestion view (needs FG-28's switcher — §4.1); copying
the person-level `USER.md` (§2).

## Testing requirements

- First run seeds a default entity goal; editing it in settings bumps the
  revision and marks profile copies stale.
- A suggestion is never auto-adopted; only the owner can adopt; a profile admin
  cannot.
- Generation is skipped while a suggestion is still `proposed` (the one-open cap),
  and skipped when the monthly interval has not elapsed; the runner-up cluster is
  neither emitted nor latched, and can win the following month.
- A suggestion without a `proposed_goal` is rejected at the store boundary, and a
  role-shaped `proposed_name` is accepted.
- Adoption creates a profile with the sub-goal, the published entity goal and
  **only** promoted skills — the parent's local skills and participation memory
  do **not** appear.
- The new profile's DSN/schema is its own (FG-27 regression).
- A dismissed suggestion is not re-proposed on the same evidence.
- Channel-less profile is fully usable from `agent-home`/CLI; `hermes doctor`
  reports it; reusing the parent's bot token is refused by collision detection.
- Retire: archive is restorable; the promotion offer fires exactly once; the
  goal is marked completed and its children are not orphaned silently.
- Person-level `USER.md` is shared with the new participation **without being
  copied** — asserted by path (`<root>/persons/<user_id>/USER.md`), so a future
  copy-on-adoption implementation fails the test; participation memory is not
  shared.

## System testing (system-test box)

On `hermes-systest`: start from one profile, generate work that clusters, run
the digest, confirm a suggestion with legible evidence, adopt it, and verify the
new profile starts with the entity goal + promoted skills and none of the
parent's memory — then retire it and confirm its skills were offered for
promotion before the archive.

## Dependencies

- **Blocked by:** FG-29 (goal tree, promotion, digest), FG-27 L3+L1 (per-profile
  schema for the new profile).
- **Related:** FG-28 (console + gateway collision detection), FG-24 amended
  (person-level vs participation-level memory).

## Definition of Done

Suggestion generated on the weekly digest with evidence; owner-only adopt/dismiss
with audit; adoption creates a correctly-seeded profile; channel-less start and
commit-to-channel; retire/merge with one-time promotion offer; idle detection;
console queue; full negative matrix on real Postgres; `scripts/run_tests.sh`,
`ruff`, `ty` clean; system test passed.

## Progress checklist

- [x] `profile_suggestions` table (role + goal + `dedup_key`) + owner-only adopt/dismiss + C5 audit
- [~] **Monthly** generation pass, one open suggestion at a time; rendered in FG-29's weekly digest (§1.1) — the interval is now enforced (edition 4); nothing *schedules* the pass, so it runs when `hermes profile suggest` is run
- [x] No re-proposal of dismissed suggestions on the same evidence — latched on `dedup_key` over the evidence's *identity* (edition 4), reusing `cron/suggestions.py`'s contract rather than a second mechanism (§1.3)
- [x] Adoption → `create_profile` with sub-goal, published entity goal, promoted skills through the shared tier; parent `.env` and un-promoted local skills **not** copied; person-level `USER.md` **asserted, not copied** (§2)
- [~] Channel-less start + `hermes doctor` reporting (channel-less is read from the profile's own `.env`, not from whether its gateway happens to be running). The **commit-to-channel step** is still the generic `hermes gateway`/Channels path — no FG-30 command exists for it
- [x] Retire/merge with one-time promotion offer + archive; owner-only, channel released, profile-tier **and** child goals completed, in the retired profile's own schema
- [x] Idle-profile detection in the digest (a just-adopted profile is not reported idle on day one)
- [x] Seeded default entity goal + settings/onboarding editor; editing bumps the publish revision (shipped by FG-29, verified here)
- [ ] **`agent-home`** (D20, **not** the dashboard): profile-local suggestion queue with evidence (§4.1). Only the console API exists (`/api/profiles/suggestions`) — the owner-facing surface is unbuilt, so today the queue is reachable from the CLI and the digest
- [~] Tests (E2E on real Postgres in `tests/hermes_cli/test_fg30_profile_suggestion_e2e.py`, plus `test_fg30_review_defects.py` for the properties that suite could not see). **System test on `hermes-systest` not run** — it needs the box, which no cloud agent can reach

## Re-read against the shipped implementation, edition 4

#250 shipped the layer this doc specifies and its shape is right — `SCHEMA_SQL`,
frozen dataclass, async store, C5 audit, `digest_lines()`, rendering (never
generating) inside `weekly_digest()`. Nine things did not hold. Each is fixed,
and each has a test that fails without the fix:

| # | What was claimed | What the code did |
|---|------------------|-------------------|
| 1 | owner-only adopt/dismiss on the console | the routes resolved `PrincipalStore.get_owner()` and then checked `principal.is_owner` — always true, so every authenticated caller acted **as the owner** and the C5 row named the owner. The routes now bind the requesting principal through `_comms_resolve_principal`, the FG-28 chokepoint |
| 2 | a dismissal is latched on `dedup_key` | the key hashed the *whole* evidence blob, including skill **use counts**, the roster and the description — all of which move, so a dismissed suggestion returned next cycle under a new key. The key now hashes the cluster's identity (skill names + unparented goal ids) |
| 3 | monthly generation | `DEFAULT_GENERATION_INTERVAL` was exported and never read; only the one-open cap bounded anything, and a dismissal frees that slot immediately. The clock is now enforced against the last proposal of *any* status, so a dismissal does not reset it |
| 4 | a suggestion follows a cluster in the work | the bar counted the profile description as a signal, so any profile with one used skill cleared it — "the system noticed a cluster" reduced to "the month elapsed". The bar is now a real skill cluster **plus** corroboration |
| 5 | adoption inherits config + promoted skills, not the parent's credentials or local skills | it called `create_profile(clone_config=True)`, which copies `config.yaml`, `SOUL.md`, **`.env`** and the parent's **installed skills** — the two things §2 lists as *not* inherited, and a copied `.env` carries the parent's credentials and resolved DSN into a profile FG-27 expects to derive its own. Adoption now copies `config.yaml` only and registers the shared skill library as an external dir |
| 6 | retire completes the profile's goal | it passed the *caller's* connection into calls made under a `set_hermes_home_override`, so the update ran against the **calling** profile's schema — the scope/identity mispairing FG-28 had to fix three times. It now opens its own connection under the override, and completes the operational children too. `retire`/`merge` were also ungated (no `is_owner` check), and a merge only archived the source: the merged-away profile kept its channel and its active goals |
| 7 | adoption seeds the new profile's sub-goal and publishes the entity goal into it | **both calls were dead.** They constructed `GoalRegistryStore()` with no argument (its `store` is required) and called `registry.update_goal(...)`, which does not exist — so each raised immediately and was swallowed by its own `except Exception: log.warning`. Retirement's goal completion had the same two faults. An adopted profile therefore got **no sub-goal and no entity goal**: the "profile with nothing to hang off" §2 exists to prevent. Now one method that publishes from *this* profile's context and seeds the sub-goal beneath the published copy through `connect_for_publish` |
| 8 | the entity goal is published into the new profile | even with the call fixed, it ran under a home override pointed at the new profile — where `publish_entity_goal` looks for the entity goal in the *new*, empty schema and skips the target as its own origin. The override is gone; the crossing is `connect_for_publish`, the one sanctioned door |
| 9 | `hermes doctor` reports channel-less profiles | it reported *gateway-not-running*, which is a different condition: a profile with a Telegram token whose gateway is stopped read as "no channel (console/CLI only)". Channel-less is now read from the profile's own `.env` |

Two notes that are not defects:

- **The evidence sent to the aux LLM includes the roster** (`user_id`, display
  name, role of every active principal). That is person data leaving the box to
  a third-party model to name a profile. It is not needed for naming; if it
  stays, it belongs in the C5 story rather than in an untracked prompt.
- **`get_store("supabase-app", "prod")` is hard-coded** in the digest and the
  store resolver, so a dev context reads prod. Consistent with the other C3
  consumers on this tier, but it is a `prod`-only assumption, not a routing one.

## Resolved decisions (were open questions)

1. **Volume and cadence — at most one open suggestion, generated at most monthly**
   (Leo, edition 3). See §1.1, including why the generation clock is separate from
   FG-29's weekly digest.
2. **Role and goal are both required** (Leo, edition 3). A role-shaped profile name
   is allowed; `proposed_goal` stays mandatory because retirement completes a goal
   and a role has no end state. See §1.2.

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo's answer to the OPC-routing question turned out to be a new capability rather than a UX choice: **support both** — a channel per profile for clarity, but starting from one or a couple of profiles because "the human may not know what kind of profile does he/she needs", with the system **suggesting more profiles over time, as part of the learning and promotion**. Every other Phase-6 doc had assumed static, up-front profile structure. Profile creation becomes an *output* of the same loop FG-29 uses for skills: the evidence that distils a skill also shows where work clusters into a distinct sub-goal. Three holes that the suggestion mechanism opens are addressed here rather than left implicit: (a) a bot token needs a human at BotFather, so a mandatory credential step would block the routine act of adopting a suggestion — adopted profiles therefore start **channel-less** and earn a channel when the owner commits; (b) suggestion without **retirement/merge** produces sprawl, and each profile costs a memory, a channel and a thing to remember, so idle detection and a retire path with a one-time promotion offer are in scope; (c) **splitting memory** between a parent and a new profile is a judgement no heuristic makes well and nobody will do by hand, so adoption deliberately inherits only the unambiguous parts (sub-goal, promoted skills, person-level `USER.md`) — lossy but honest and automatable, and it gives skill promotion a second purpose, since a promoted skill is what a new instrument starts life with. | Leo: "We need to support both. Each profile should have its own bot/channel to make things more clear and efficient for both the human and the system. However, at the beginning, the human may not know what kind of profile does he/she needs. Therefore, the system should be able to start with just one profile or a couple of profiles and the ability to suggest more profiles to add over time, as part of the learning and promotion." |
| 2026-08-14 | 3 | devin (for Leo) | Leo's two open questions closed (one open suggestion, monthly; role **and** goal both required) and three pickup defects fixed: the shipped `cron/suggestions.py` surface, the `USER.md` inheritance that is already true, and the unnamed UI surface | Leo answered both open questions, and answering the cadence one broke the doc's own wiring: it said suggestion generation "runs on the same weekly digest" as skill promotion, but **monthly** generation cannot share a weekly clock. Split explicitly — generation is its own monthly pass gated on no suggestion being open, rendering still rides `weekly_digest()`, which schedules nothing — because "same digest" would otherwise be implemented as "weekly", i.e. four times the intended volume against a mechanism whose dismissals latch forever. Role+goal are both required for a reason worth recording: a role has no end state, and §4's retire path fires when a sub-goal *completes*, so a role-only suggestion could never retire and would produce exactly the sprawl this FG bounds. Three defects found by reading the doc against shipped code rather than trusting it: (a) **`cron/suggestions.py` already implements this pattern** — consent-first proposals from four sources with `dedup_key`-latched dismissals — and the doc specified a fresh non-repetition rule, i.e. a second latching mechanism, which `AGENTS.md` rejects; the contract is now reused and the separate store is argued (JSON file vs `evidence` JSONB + goal/principal FKs) instead of assumed; (b) the "inherit the person-level `USER.md`" item is **already true** — FG-24 edition 3 put it at `<root>/persons/<user_id>/USER.md`, outside any profile home, so an implementer reading "inherited" as "copy on adoption" would reintroduce the drifting-copies problem that amendment exists to remove; it is now an assertion, and the test asserts by path; (c) the UI surface was "console", which under **D20** must be `agent-home` — read as the dashboard it would have put this FG's main surface in the frozen operator console. Also recorded: the queue is **profile-local** because `profile_suggestions` FKs profile-local `goals`/`principals`, and a cross-profile view needs FG-28's unshipped switcher — the FG-26 item-1 trap, named so nobody walks into it again. |
| 2026-08-14 | 4 | devin (for Leo) | Reviewed the shipped implementation (#250); nine defects fixed and the checklist re-marked honestly | Leo asked for a review of the implementation. The layer's shape follows `skill_promotion.py` correctly, so the defects were all in the *properties*, not the structure — and every one of them was invisible to the suite that shipped with it, because those 8 tests exercise the store's CRUD with hand-written evidence dicts and a hand-written `dedup_key`. The latch test, for instance, proposes the *same literal dict twice*, so it cannot see that a key hashed over skill use counts changes every week; the routes were never instantiated, so "owner only" gating nothing was invisible for the same reason FG-26's activation bug and FG-28's three route defects were. The one that would have hurt most in production is adoption calling `create_profile(clone_config=True)`: that copies the parent's `.env` — credentials and resolved DSN — and its un-promoted local skills into the new profile, i.e. exactly the two items §2 lists as not inherited, while the docstring asserted the opposite. Three checklist items were also ticked without the work: the `agent-home` queue (rewritten in the tick to "dashboard can build UI", which inverts D20), the commit-to-channel step, and "system test" — ticked on the strength of Docker-Postgres E2E tests, where §System testing means the live box. Those are back to open/partial. The two worst were only visible to a type checker: `ty` reports that both goal-tree call sites construct `GoalRegistryStore` without its required `store` and call a `update_goal` method that does not exist — each wrapped in its own `except Exception: log.warning`, so adoption produced a profile with no sub-goal and no entity goal, and retirement completed nothing, both silently and both green. |
| 2026-08-10 | 2 | devin (for Leo) | First goal seeded + editable in `agent-home` settings; invitation delivery recorded as a decision, not a hole | Leo closed the two smaller onboarding gaps. The **first goal** is seeded from a system default and edited in settings — which matters more than it sounds: an entity goal nobody wrote means publication, roll-up, conflict detection and skill scoring all have nothing to hang off, and a *seeded* generic goal invites replacement where an empty field invites being skipped. The settings page is therefore a writer into the goal tree — an edit bumps the publish revision and marks every profile's copy stale (FG-29 §3), rather than being a text box. The **invitation link** is shared by the owner through their own channel, so the missing SMTP is a decision rather than a gap; the cost is written down here instead of being forgotten — a relayed link sits in a chat app's scrollback and the relaying owner could activate the account themselves, so "the user set their own password" is not an integrity property this deployment can claim. | Leo: "The first goal can come from the system default, but also must be configurable at the settings page in the agent-home. The invitation link can be shared by the owner using his/her own mean." |

## Cloud-agent prompt

> Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read
> `docs/design/master-plan/README.md`, `AGENTS.md`, FG-24 (incl. its
> amendment), FG-27, FG-28, FG-29 and this doc.
>
> **Reuse, do not rebuild.** `hermes_cli/profiles.py` already has
> `create_profile(name, clone_config=..., description=...)`, `delete_profile`,
> export/import and `profile.yaml` meta; `hermes_cli/profile_describer.py`
> already generates "what this profile is good at" from skills+model via the
> aux LLM; `agent/background_review.py` + `tools/skill_usage.py` already produce
> the evidence. This FG is a reviewed proposal layer over those.
>
> **Read `cron/suggestions.py` first.** It already implements consent-first
> proposals with `dedup_key`-latched dismissals. Reuse that contract and its
> vocabulary; do **not** build a second latching mechanism. The store is separate
> for the reasons in §1.3, and only for those reasons.
>
> Add `profile_suggestions` (schema above — `proposed_role` **and**
> `proposed_goal` both required). **Generation is its own monthly pass**, skipped
> while any suggestion is still `proposed` (at most one open at a time), never on
> an interrupt, never auto-adopted, **owner-only** adopt, dismissals retained and
> latched. `weekly_digest()` in `hermes_cli/goal_conflicts.py` only **renders** an
> open suggestion — it returns `(title, lines)` and schedules nothing, so do not
> put generation inside it.
>
> Adoption calls `create_profile` and seeds: the sub-goal, the published entity
> goal and **only promoted skills from the shared tier**. The person-level
> `USER.md` needs **no work** — FG-24 put it at `<root>/persons/<user_id>/USER.md`,
> outside the profile home; **assert** it is visible and do not copy it (a copy is
> the drift bug that amendment removed). It must NOT copy the parent's
> participation memory, local skills or resolved DSN (FG-27). Do not implement
> memory splitting — it is deliberately out of scope.
>
> Every user-facing screen goes in **`agent-home/`, not `web/`** (D20). The queue
> is **profile-local**: `profile_suggestions` FKs profile-local `goals`/
> `principals`, and FG-28 has **not** shipped a profile switcher, so do not attempt
> a cross-profile view.
>
> Adopted profiles start **channel-less** (`agent-home`/CLI usable), reported by
> `hermes doctor`, and gain a channel only on a deliberate commit step; verify
> the gateway's same-token collision detection refuses reusing the parent's bot.
>
> Implement retire and merge: archive via the existing export path, release the
> channel, mark the goal completed, and offer the profile's skills for promotion
> **exactly once** before archiving. Flag idle profiles in the digest.
>
> Tests on real Postgres: adoption seeding (positive and the four "must not
> appear" negatives), owner-only authorization, dismissal non-repetition,
> channel-less usability, token-reuse refusal, retire promotion-offer-once, and
> the person-level/participation-level memory boundary. Then the
> `hermes-systest` procedure above. `scripts/run_tests.sh`, `ruff`, `ty` clean.
