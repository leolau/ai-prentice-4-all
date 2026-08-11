# FG-30 — Profile lifecycle: suggest, adopt, retire

**Wave:** P6-D (after FG-29 — suggestion is an output of the same loop) · **Owner agent:** _unassigned_ · **Status:** PLAN — not started

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

Runs on the **same weekly digest** as skill promotion (FG-29 §6) — one review
moment, not two. A suggestion is a proposal with evidence attached, never an
automatic creation:

```sql
profile_suggestions(
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  proposed_name    TEXT NOT NULL,
  proposed_goal    TEXT NOT NULL,       -- the sub-goal it would serve
  parent_goal_id   UUID NULL REFERENCES goals(id),
  rationale        TEXT NOT NULL,       -- the evidence, in the owner's language
  evidence         JSONB NOT NULL,      -- skills / goals / session ids behind it
  origin_profile   TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'proposed'
                   CHECK (status IN ('proposed','adopted','dismissed')),
  reviewed_by      TEXT NULL REFERENCES principals(user_id),
  reviewed_at      TIMESTAMPTZ NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

**Only the owner adopts** — a new profile is a change to the entity's goal tree.
Dismissals are kept, and a dismissed suggestion must not be re-proposed on the
same evidence; sprawl by nagging is the failure mode to design against.

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
| the person's person-level `USER.md` (FG-24 amended) | the parent's participation-level memory |
| model/provider config via the existing `--clone-config` path | the parent's `.env` credentials (FG-27: never inherit a resolved DSN) |

Lossy and honest. And it gives the promotion path a second purpose: **a skill
that was promoted is a skill a new profile starts with**, so the org tier is
what makes each new instrument better than the first one was.

Reuse: `create_profile(name, clone_config=..., description=...)` already exists
and already takes a description; `profile.yaml` already carries
`description` + `description_auto`; `hermes profile describe --auto` already
generates that description from skills+model via the aux LLM. Adoption is
mostly wiring existing calls behind a reviewed proposal.

### 3. Channels: a suggested profile starts channel-less

Leo's decision is that each profile should have its own bot/channel — clearer
for the human and for the system, and it removes any need for the gateway to
*infer* which sub-goal a message belongs to (a wrong guess writes context into
the wrong brain, which is worse than an extra chat window).

But a bot token requires a human at BotFather. **If profile creation is meant to
be routine and system-suggested, a mandatory credential step is exactly what
will stop people doing it.** So:

- an adopted profile is immediately usable from the **console and CLI**, with no
  channel and no credential;
- it gains a channel when the owner commits to it — a deliberate act, at which
  point the credential friction is proportionate;
- `hermes doctor` reports channel-less profiles so they don't get forgotten;
- the same-token collision detection in the gateway (FG-28) already refuses two
  profiles polling one bot, so the "just reuse the token" shortcut fails loudly
  rather than silently interleaving two sub-goals.

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
- FG-29 — goal publication, the promotion path, the weekly digest.
- FG-27 — never inherit a resolved DSN; new profile gets its own schema.
- FG-28 — the console renders the queue; gateway collision detection.
- `agent-home/src/app/settings` + `components/settings/SettingsView.tsx` and the
  existing `onboarding` route — the first-goal editor, already built.
- FG-26 invitations (delivery is the owner's own channel, by decision).
- C5 audit; C12 change management.

## Scope

**In:** a **seeded default entity goal** editable in `agent-home` settings (an
edit is a publication event); `profile_suggestions` with evidence and owner-only adoption; suggestion
generation on the weekly digest; adoption wiring `create_profile` with sub-goal
+ promoted skills + person-level `USER.md`; channel-less start and the
commit-to-channel step; retire/merge with a one-time promotion offer; idle
detection; console queue; `hermes doctor` reporting.

**Out:** automatic profile creation without review; memory splitting or merging
(deliberately — §2); automatic channel provisioning (needs a human at the
platform); cross-entity profile templates; re-proposing a dismissed suggestion
on the same evidence.

## Testing requirements

- First run seeds a default entity goal; editing it in settings bumps the
  revision and marks profile copies stale.
- A suggestion is never auto-adopted; only the owner can adopt; a profile admin
  cannot.
- Adoption creates a profile with the sub-goal, the published entity goal and
  **only** promoted skills — the parent's local skills and participation memory
  do **not** appear.
- The new profile's DSN/schema is its own (FG-27 regression).
- A dismissed suggestion is not re-proposed on the same evidence.
- Channel-less profile is fully usable from console/CLI; `hermes doctor` reports
  it; reusing the parent's bot token is refused by collision detection.
- Retire: archive is restorable; the promotion offer fires exactly once; the
  goal is marked completed and its children are not orphaned silently.
- Person-level `USER.md` is shared with the new participation; participation
  memory is not.

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

- [ ] `profile_suggestions` table + owner-only adopt/dismiss + C5 audit
- [ ] Suggestion generation on the FG-29 weekly digest (skills/goals/describer signals)
- [ ] No re-proposal of dismissed suggestions on the same evidence
- [ ] Adoption → `create_profile` with sub-goal, published entity goal, promoted skills, person-level `USER.md`
- [ ] Channel-less start; commit-to-channel step; `hermes doctor` reporting
- [ ] Retire/merge with one-time promotion offer + archive
- [ ] Idle-profile detection in the digest
- [ ] Seeded default entity goal + settings/onboarding editor; editing bumps the publish revision
- [ ] Console: suggestion queue with evidence
- [ ] Tests + system test

## Open questions

1. **How many suggestions per digest is too many?** One is a nudge; four is
   noise that trains the owner to dismiss without reading. Cap at one or two?
2. **Should a suggestion be allowed to name a profile that mirrors a role
   rather than a sub-goal** ("CFO" vs "improve cashflow")? The domain model says
   sub-goal, but people think in roles, and in an OPC the two coincide.

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo's answer to the OPC-routing question turned out to be a new capability rather than a UX choice: **support both** — a channel per profile for clarity, but starting from one or a couple of profiles because "the human may not know what kind of profile does he/she needs", with the system **suggesting more profiles over time, as part of the learning and promotion**. Every other Phase-6 doc had assumed static, up-front profile structure. Profile creation becomes an *output* of the same loop FG-29 uses for skills: the evidence that distils a skill also shows where work clusters into a distinct sub-goal. Three holes that the suggestion mechanism opens are addressed here rather than left implicit: (a) a bot token needs a human at BotFather, so a mandatory credential step would block the routine act of adopting a suggestion — adopted profiles therefore start **channel-less** and earn a channel when the owner commits; (b) suggestion without **retirement/merge** produces sprawl, and each profile costs a memory, a channel and a thing to remember, so idle detection and a retire path with a one-time promotion offer are in scope; (c) **splitting memory** between a parent and a new profile is a judgement no heuristic makes well and nobody will do by hand, so adoption deliberately inherits only the unambiguous parts (sub-goal, promoted skills, person-level `USER.md`) — lossy but honest and automatable, and it gives skill promotion a second purpose, since a promoted skill is what a new instrument starts life with. | Leo: "We need to support both. Each profile should have its own bot/channel to make things more clear and efficient for both the human and the system. However, at the beginning, the human may not know what kind of profile does he/she needs. Therefore, the system should be able to start with just one profile or a couple of profiles and the ability to suggest more profiles to add over time, as part of the learning and promotion." |
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
> Add `profile_suggestions` (schema above) generated on FG-29's **weekly
> digest** — never on an interrupt, never auto-adopted, **owner-only** adopt,
> dismissals retained and not re-proposed on the same evidence.
>
> Adoption calls `create_profile` and seeds: the sub-goal, the published entity
> goal, **only promoted skills from the shared tier**, and the person-level
> `USER.md`. It must NOT copy the parent's participation memory, local skills or
> resolved DSN (FG-27). Do not implement memory splitting — it is deliberately
> out of scope.
>
> Adopted profiles start **channel-less** (console/CLI usable), reported by
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
