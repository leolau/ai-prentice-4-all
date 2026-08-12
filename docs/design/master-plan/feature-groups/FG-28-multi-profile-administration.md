# FG-28 — Multi-profile administration (one admin, several profiles)

**Wave:** P6-C (Phase-6 follow-on — after FG-26; requires FG-27 Layers 3+1). **Also carries the one-gateway-for-all-profiles consolidation.** · **Owner agent:** _unassigned_ · **Status:** IN PROGRESS — item 1 done (the `os.environ` leaks, #219 + #220); items 2–9 open

## Reframing (2026-08-10) — this is the console over the goal tree

The domain model changed after this FG was written, and it changes what the FG
*is* more than what it builds.

A **profile is not a tenant and not a cohort of people** — it is the *instrument
for one sub-goal*, carrying the behaviour that sub-goal needs (FG-29). **People
participate in as many profiles as their work spans**, holding a `principals`
row in each under one shared GoTrue subject, with separate memory in each.

Three consequences:

1. **The "users belong to exactly one profile" constraint below was imported,
   not imposed.** The same person can already hold rows in `finance` and
   `product`. What is per-profile is their *data*, which is the isolation we
   want. A CTO who works on cashflow and product quality is one login and two
   working memories — no groups, no cross-profile identity work.
2. **This is no longer "administration spanning tenants."** It is one console
   over the entity's goal tree: the owner sees the whole tree, and each person
   sees the participations they hold. Administration is a consequence of that
   view, not its purpose.
3. **It is also a runtime consolidation**, not only a UI — see "One gateway for
   all profiles" below, which removes the per-profile daemon cost.

The authority model, the routing, the owner-fallback hazard and the account-vs-
enrolment split below are all unchanged by the reframing. Read them as written;
only the *motivation* section above is superseded.

## Summary

The owner runs several profiles — `engineers`, `testers`, `hr`. Each is an
isolated brain with its own users, memory and database. The requirement is that
**one administrator can create and manage users in several of them**, and that
the owner can choose which:

```
profiles:   engineers      testers        hr
admins:     CTO ───────────CTO            CFO
users:      Adam           Mary           John
```

CTO administers `engineers` and `testers`; CFO administers `hr`; neither can
touch the other's. Adam, Mary and John each remain in **exactly one** profile.

**This is not cross-profile identity for users.** Nothing a user can read or
write crosses a profile boundary; the brains, memories and databases stay as
isolated as they are today. The only thing that crosses is *administration*.

That distinction is the whole design. It is also what makes the feature
defensible against the repo's standing rule that profiles are independent
islands: we are not coupling the *runtimes*, we are adding a control plane above
them.

## Why this needs its own FG (and an explicit decision)

`AGENTS.md` records profile independence as deliberate, and cites a closed PR
that added cross-profile config inheritance: *"coupling profiles together is
exactly what the design prevents."* FG-28 moves in the opposite direction from
FG-27, which **strengthens** the boundary. So it must be argued, not assumed.

The argument for doing it anyway: today the boundary is enforced by *physical
separation* (separate processes, separate consoles, separate URLs). An operator
with three profiles either logs into three consoles or, far more likely, gives
one person the owner credential for all three — which is strictly worse than a
scoped console. **FG-28 replaces an informal boundary that people route around
with a formal one the code enforces.** But it does move the boundary from "two
processes cannot see each other" to "one service must get this right", and that
is a real increase in the consequences of a bug.

## What already exists (this is cheaper than it looks)

Four pieces of the mechanism are already in the tree.

**1. The entitlement model needs no new tables.** The Supabase dashboard-auth
provider verifies GoTrue's access token and uses the `sub` claim as the
identity; `hermes_cli/access.py` uses that same UUID as `principal.user_id`.
**All profiles share one Supabase instance (decided 2026-08-10)**, therefore one
GoTrue, so one login produces one subject that is meaningful in every profile — and each profile's own `principals` table decides
whether that subject is enrolled and with what role:

```
CTO has an `admin` principal row in engineers   → may administer engineers
CTO has an `admin` principal row in testers     → may administer testers
CTO has NO row in hr                            → may not administer hr
```

"Assign a profile to an admin" is therefore just `hermes member add` in that
profile. **The existing per-profile `principals` table *is* the entitlement
list**, it is already RLS-protected, and it fails closed by construction —
absence of a row is absence of authority. No new authority model, no new role
type, nothing to keep in sync.

**2. The fail-closed behaviour is already implemented.** `_comms_resolve_principal`
raises **409** for a subject that authenticates but is not enrolled — it is
explicitly documented as "never silently upgraded to the owner". CFO's request
against `hr` is already rejected by the code path a console would reuse.

**3. Per-request profile scoping already exists.** `set_hermes_home_override()`
is a `contextvar`, `get_hermes_home()` honours it, and `load_config()` caches on
the *config path* so a profile switch cannot collide. With FG-27 Layer 3, the
schema derives from `get_active_profile_name()`, which reads the same
contextvar. So scoping a request to a profile is, in principle, a `with`-block.

**4. `profiles_to_serve(multiplex)` is already the single chokepoint** for
"which profiles does this process serve", written precisely so later
multiplexing phases need not re-derive the set.

## The blocker: secrets are process-global, and `HERMES_HOME` is not

This is the finding that determines the architecture, and it is not fixed by
FG-27.

`HERMES_HOME` is context-local. **`os.environ` is not.** And the app DSN on the
live box is resolved *through* the environment:

```yaml
# $HERMES_HOME/config.yaml
datastore:
  supabase_app:
    dsn: ${DATABASE_URL}
```

```python
# hermes_cli/config.py — _expand_env_vars
re.sub(r"\${([^}]+)}", lambda m: os.environ.get(m.group(1), m.group(0)), obj)
```

`_expand_env_vars` reads **`os.environ`**, and `reload_env()` *writes* to
`os.environ` — process-globally. Switching the `HERMES_HOME` contextvar changes
which `config.yaml` is parsed, but **not** which `DATABASE_URL` that file's
`${DATABASE_URL}` resolves to.

So in a single process serving several profiles:

- every profile's app DSN resolves to whichever `.env` was loaded last;
- the same is true of `SUPABASE_SERVICE_ROLE_KEY` — the credential that can mint
  and delete **any** account — and of every model API key;
- there is no context-local seam to fix it behind: **~2,250 `os.getenv` /
  `os.environ` call sites** outside tests.

Two consequences:

1. **A single-process multi-profile console cannot safely hold per-profile
   secrets.** This is architectural, not a bug to be fixed in passing.
2. **FG-27 Layer 3 is load-bearing for this FG.** With per-profile schemas, a
   profile that resolves the wrong DSN lands in a *different schema of the wrong
   database* — wrong, detectable, and not a data merge. Without it, the same
   mistake is a silent merge. FG-27 turns this failure mode from catastrophic to
   merely broken.

**Correction after the shared-Supabase decision — this is a strong
recommendation, not a hard blocker.** Being honest about the weakened argument:
if all profiles share one Supabase, then `DATABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY` are **the same value for every profile**, so the
process-global environment cannot make them wrong. The constraint above stops
being a correctness blocker for the datastore.

What survives:

- **Per-profile secrets that genuinely differ** — model API keys are the live
  example (a profile may use a different provider or a separately-billed key),
  and any future per-profile credential. Those still collapse to whichever
  `.env` loaded last.
- **The coupling is invisible and unenforced.** Nothing declares "these secrets
  must be identical across profiles"; the day one profile is given its own
  Postgres or its own key, the multiplexed process silently uses the wrong one.
  A design that is correct only while a coincidence holds is a latent bug with a
  delayed trigger.

That argued for fan-out when it was written, because the process boundary made
the property structural instead of coincidental. **It has since been decided the
other way — see §"Architecture decision" — and the condition attached here is
what that decision must satisfy:** an explicit, tested assertion that each served
profile resolves *its own* values, failing closed rather than silently sharing.
`get_secret` now provides the failing-closed half; the test is item 8 of the
checklist.

**Latent issue to verify first (not introduced by this FG).** The gateway
already multiplexes profiles in one process, scoping each turn with
`set_hermes_home_override()`. By the reasoning above, profiles served that way
already share one `os.environ`, so a multiplexed gateway whose profiles have
different `DATABASE_URL`s or API keys resolves them globally rather than
per-profile. This is a **hypothesis from reading the code, not an observed
failure** — the live box has one profile, so it cannot be exhibiting it.
Confirming or refuting it is the **first task** of this FG, because if it is
real it is a bug in shipped code and outranks the feature.

### Verified 2026-08-12 — **confirmed, on three paths, and closed**

Two profile homes with deliberately different `.env` values, one process, real
`profile_runtime_scope()`, real files on disk. The already-migrated seams
(`get_secret`, `runtime_provider`, MCP `${VAR}` interpolation) held. Three
unmigrated paths did not, and every one of them decides a credential that a
turn actually uses:

```
config ${VAR} expansion   dsn/service_role_key → the PROCESS value, both profiles
gateway platform tokens   telegram token       → the PROCESS value, both profiles
resolve_anthropic_token   ANTHROPIC_API_KEY    → the PROCESS value, both profiles
```

Each is worse than "reads the wrong variable":

- **`_expand_env_vars`** is applied once per config load and the *expanded*
  result is cached per config path. A single unscoped load of a profile's config
  (`hermes status` walking every profile) therefore left the process-env
  expansion in the cache, and that profile's next turn was served it. The cache
  key now includes a fingerprint of the active secret scope.
- **`_apply_env_overrides`** applies every platform token as an override on top
  of the profile's own `config.yaml`, so the process environment *beat* each
  profile's `.env`. Both profiles resolved one token — which then either polls
  the wrong bot, or is refused by the same-credential collision check as a clash
  with a profile it shares no credential with. The 28 credential-shaped reads in
  that function now go through the scope; the tuning knobs beside them still read
  `os.environ`, which is correct for a deployment-level override.
- **`resolve_anthropic_token`** is the provider fallback, so a turn could be
  billed to, and authenticated as, another profile's account. Now fails closed.

A fourth, found while probing and the most damaging of the four: **any per-turn
call to `load_hermes_dotenv()` writes a whole `.env` into `os.environ` with
`override=True`** (two such callers exist on the MCP config path). In a
multiplexer that makes the process environment become the last profile to reach
that line, poisoning every unscoped read and every subprocess. It is now refused
while multiplexing is active — the interpolation those callers wanted is already
scope-aware.

### The subprocess seam — closed 2026-08-13

A contextvar does not cross a process boundary, so every spawn built its child's
environment from `os.environ` — which in a multiplexer is the **default
profile's** `.env`, loaded at import time by `gateway/run.py` before any turn
exists. `secret_scope` not mutating `os.environ` kept one secondary profile from
seeing another's values, but it could not give a child the *right* ones.

The exposure was narrower than the raw statement suggests, and worth recording
because the narrowing is deliberate design that already existed: every spawn
surface strips credentials by default (`_HERMES_PROVIDER_ENV_BLOCKLIST`,
`_ALWAYS_STRIP_KEYS`, `_is_hermes_internal_secret`), and MCP stdio spawns
allowlist their environment outright. What remained were the two paths that pass
a credential through *on purpose*: `inherit_credentials=True` for a blessed
model-driving CLI (`claude`, `codex`, `gemini`), and a skill-registered
`env_passthrough` key in the terminal. Both handed the child the default
profile's value, so a secondary profile's turn would have authenticated and
billed as the default profile.

The fix keeps provenance rather than guessing it. `load_hermes_dotenv` now
records which names it wrote into `os.environ`
(`secret_scope.note_env_file_keys`), because once loaded, a profile-owned value
and one the operator exported in the unit file are indistinguishable strings —
and only the former may be corrected. `apply_scope_to_subprocess_env` then, under
an active scope, replaces a surviving key with the scope's value and **drops** an
env-file-derived key the profile does not define, so a child fails closed rather
than inheriting a stranger's credential. It runs on the `os.environ`-derived
layer of all three builders (`_make_run_env`, `_sanitize_subprocess_env`,
`hermes_subprocess_env`) and only ever overrides or removes — it cannot re-admit
a key a spawn surface stripped, and it is a no-op with no scope installed, which
is every single-profile deployment.

Still not scope-corrected, and correctly so: the terminal's session snapshot is
a file written by the child shell and re-sourced by later commands in the *same*
backend instance, which belongs to one session and therefore one profile.

None of this was exhibiting on the box: `gateway.multiplex_profiles` is false
and `hermes-systest` serves one profile. It was a real defect in shipped code
that the first profile to be given its own credentials would have triggered.

## One gateway for all profiles (decided 2026-08-10)

**In scope for this FG: run a single gateway per box, serving every profile.**
The alternative — one daemon per profile — costs a measured **150 MB resident
each** before any conversation, on top of ~225 MB for a per-profile console. At
ten sub-goal profiles that is ~3.7 GB standing still, against 9.6 GB available
on the current box.

**The mechanism already exists and is substantially implemented.** Measured and
read on 2026-08-10:

- `gateway.multiplex_profiles` (default **false**, preserving one-gateway-per-
  profile) turns it on; `profiles_to_serve(multiplex=True)` is the single
  chokepoint for the served set.
- `_profile_runtime_scope(profile_home)` in `gateway/run.py` combines the two
  seams a multiplexer needs: `set_hermes_home_override` (config, skills, memory,
  SOUL, sessions) **and** `set_secret_scope` (that profile's `.env`).
- `agent/secret_scope.py` is the answer to the process-global environment
  problem described above: a **context-local** secret scope that propagates into
  the agent worker thread via `copy_context()`, deliberately does **not** mutate
  `os.environ` (a spawn's own environment is corrected per profile instead — see
  §"The subprocess seam"), and
  **fails closed** — when multiplexing is active an unscoped read raises
  `UnscopedSecretError` rather than silently returning the wrong profile's value.
- Same-credential collision detection: two profiles polling one bot token is
  refused at startup, at the only point that sees every profile's resolved
  credentials together.
- Port-binding platforms (`webhook`, `api_server`, `feishu`, `wecom_callback`,
  `bluebubbles`, `sms`, `msgraph_webhook`) are restricted to the default
  profile, which owns the single shared listener and serves the rest under a
  `/p/<profile>/` URL prefix. A secondary profile enabling one is a hard startup
  error, not a silently dropped adapter.
- Served profiles are recorded in runtime status for `hermes status`.

**So the work here is not implementation — it is finishing the migration.** The
fail-closed guarantee only protects callers that go through `get_secret()`, and
only **6 call sites** currently do, against ~2,250 direct `os.getenv`/
`os.environ` reads in the tree. An unmigrated `get_secret` caller fails loudly;
an unmigrated `os.getenv` caller **silently returns the wrong profile's value**.
That asymmetry is the whole risk.

Tasks:

- Audit every credential-reading path reachable from a gateway turn — provider
  keys, platform tokens, MCP server env, terminal backends, subprocess spawns —
  and migrate each to `get_secret()`. Prioritise by whether the value can differ
  per profile: bot tokens and model keys genuinely do; the app DSN and
  service-role key do not (one shared Supabase), so they are not urgent.
- A test that runs two profiles with **different** bot tokens and model keys
  through one gateway and asserts each turn resolves its own — the regression
  test the migration needs.
- Then enable `gateway.multiplex_profiles` on the box and re-measure.

## Architecture decision (2026-08-13) — one process, profile-scoped per request

Editions 1–3 recommended fan-out; edition 4's pickup prompt said multiplex. That
was a contradiction in the doc, not two readings of one plan, and it is resolved
here in favour of **one process serving every profile, entering the target
profile's scope per request**. Three reasons, in order of weight:

1. **Fan-out's load-bearing argument was "a contextvar cannot keep secrets apart,
   only the process boundary can." That is no longer true.**
   `set_secret_scope` is context-local and `get_secret` **fails closed** when
   multiplexing is on — an unscoped read raises rather than returning the wrong
   profile's value — and a spawned child's environment is corrected per profile
   (#219, #220). Remove that premise and fan-out's case is cost and deployment
   complexity in exchange for a property the seam already provides.
2. **Fan-out is incompatible with the one-gateway consolidation this same FG
   carries**, which Leo asked for on measured evidence. With
   `gateway.multiplex_profiles` on, the port-binding platforms — `api_server`,
   `webhook` and friends — are a hard startup error for a secondary profile: the
   default profile owns the single listener and serves the rest under
   `/p/<profile>/`. The per-profile HTTP endpoints a console would fan out to
   therefore *stop existing* once the consolidation lands. The FG cannot build
   both.
3. **"Keep one process per profile, exactly as today" was factually wrong for the
   console tier.** There is exactly one `hermes-dashboard` unit on the box,
   serving one `HERMES_HOME` — which is *why* FG-26's picker can only see the
   current profile. Fan-out is not the status quo here; it is N new units, N
   ports and a per-profile TLS/routing story, at ~225 MB each.

**What the decision must not lose.** Fan-out's real prize was never the secrets —
it was that **authority is re-derived at the destination**, structurally, because
a buggy console physically could not reach a profile the caller has no row in. In
one process that stops being a boundary and becomes a discipline, so it has to be
made structural by other means. These are requirements, not advice:

- **Every console-routed request enters the target profile's scope** —
  `set_hermes_home_override` *and* `set_secret_scope`, the pair
  `_profile_runtime_scope()` already establishes — before any resolution runs. One
  chokepoint, so "which profile am I in" is never a parameter threaded through
  handlers.
- **The principal is re-resolved inside that scope**, from the target profile's
  own `principals` table. The picker is a routing hint and never a grant; no row
  there is a 403/409 regardless of what the caller holds elsewhere.
- **Owner-fallback is refused on those routes** — see §"The most dangerous hole",
  which matters *more* here than under fan-out, not less: the hop is now an
  in-process call, so nothing about it looks remote enough to be suspicious.
- **FG-27's schema-ownership guard is the backstop.** A wrongly-scoped request
  fails on connect instead of reading another profile's rows, which is what makes
  a scoping bug loud rather than silent.
- **The credential migration comes first.** Only 6 of ~2,250 env reads go through
  `get_secret`; an unmigrated `os.getenv` returns the wrong profile's value
  silently. Migrate every credential read reachable from a console route *before*
  serving a second profile from one process. The risk is lower than the
  gateway's — the DSN and service-role key are identical across profiles on one
  Supabase — but "identical today" is exactly the coincidence §above warns about.
- **The service-role key argument now runs in our favour.** One process holding
  the box-wide account key is one place to protect instead of N, which is the
  shape §"global accounts, local authority" already asked for.

**Rejected alternative — fan-out.** Kept because the reasoning is the relevant
contrast, and because if the credential migration proves intractable this is the
fallback:

```
                    ┌──────────────────────────┐
   browser ────────▶│  admin console (BFF)     │  one URL, profile switcher
                    │  • reads profile registry│
                    │  • forwards the caller's │
                    │    identity, per profile │
                    └───┬──────────┬───────────┘
                        │          │
              ┌─────────▼──┐   ┌───▼────────┐   ┌────────────┐
              │ engineers  │   │ testers    │   │ hr         │   unchanged,
              │ API + DB   │   │ API + DB   │   │ API + DB   │   process-isolated
              └────────────┘   └────────────┘   └────────────┘
```

Why fan-out looked better when this was written:

- **The process boundary is what keeps secrets apart** — the one thing a
  contextvar could not do at the time, and now can.
- **No change to the datastore router.** Each profile API resolves its own
  config and DSN exactly as it does today.
- **It preserves the "independent islands" intent.** The islands keep their own
  runtimes; only a registry is shared — the same shape the repo already accepted
  for `kanban.db` at the shared root.
- **Authorisation is re-derived at the destination.** The console's profile
  picker is a routing hint, never a grant: each profile API resolves the
  caller's principal *in its own `principals` table* and 409s if absent. A
  compromised or buggy console cannot manufacture authority in a profile where
  the caller has no row.

**Profile registry.** A small control-plane record at the shared root listing
which profiles exist and how to reach each one (name, base URL, health). It
cannot live inside a profile, since each only knows about itself. It holds **no
authority data** — deliberately: authority stays in each profile's `principals`
table, so there is nothing to keep in sync and no second place to get wrong.

## The most dangerous hole: the owner fallback

`_comms_resolve_principal` falls back to **the enrolled owner** when a request
carries no interactive session:

> *Fallback: a request with no interactive session (an internal caller, a
> token-authed service, or a test client) resolves to the enrolled owner.*

That is correct today, where the only sessionless callers are local internal
ones. On any console-routed request it becomes an **escalation vector**: if the
caller's identity is not carried into the target profile's scope — dropped on one
code path, or a token accepted without a subject — the request resolves to **that
profile's owner**. CFO's misrouted request against `engineers` would not be
denied; it would be executed as the owner of `engineers`.

The in-process decision makes this sharper, not softer. A fan-out hop is an HTTP
call with a visible identity header, so a missing identity is conspicuous; an
in-process entry into another profile's scope carries whatever ambient state the
handler happens to hold, and looks like a function call.

Requirements, all of them tests:

- The console route **must** carry the caller's verified subject into the target
  profile's scope, and resolution there must **refuse owner-fallback** — no
  verified subject on those routes is a 401, never an owner.
- Verify the caller's **original GoTrue access token** at the destination, the
  same JWT a browser would present, so identity is never *asserted* by the
  routing layer — the property that holds whether the hop is a socket or a call.
- Negative test: a console request with the identity header stripped must fail
  closed, not fall back.

This single behaviour is the difference between a scoped console and a root
console with a dropdown.

## Scope

**In:**

- profile registry at the shared root (`hermes profile registry` CRUD, health);
- one console with a profile switcher listing **only** profiles where the caller
  holds an `admin`/`owner` principal row, each request entering the target
  profile's scope at one chokepoint;
- identity carried into that scope + owner-fallback refusal on console routes;
- the credential-read migration for every path a console route reaches, before a
  second profile is served from one process;
- FG-26's create-user form: the read-only "creating in: X" label becomes a real
  picker over the caller's administered profiles;
- audit: every cross-profile administrative action records actor, target
  profile and target user (C5), in the **target** profile's ledger.

**Out:**

- users spanning profiles (still exactly one profile per user);
- shared memory, skills or agent state across profiles;
- in-process multiplexing of profiles in the web tier (see the blocker);
- a second authority model — entitlement remains the `principals` row.

## Prerequisites and open decisions

1. ~~Do all profiles share one GoTrue?~~ **Answered 2026-08-10: yes, one shared
   Supabase instance.** The simplification in §"What already exists" holds. It
   also brings the account-level authority problem below, and makes FG-27
   Layer 3 an absolute prerequisite rather than a hardening measure (one
   Supabase = one Postgres = every profile on the same DSN, so without
   profile-derived schemas a second profile merges into the first on contact).
2. **FG-27 Layers 1+3 must be merged** (see above).
3. **Verify or refute the multiplexed-gateway environment issue** before
   building anything.
4. One URL with a switcher (assumed here) vs. per-profile consoles with
   separate logins. The latter is nearly free and needs no registry — worth
   pricing as the fallback if (1) turns out badly.

## Testing requirements

- **Negative matrix, real Postgres:** CFO cannot list, create, update, delete or
  invite in `engineers`/`testers`; CTO cannot in `hr` — asserted at the profile
  API, not only in the console.
- **Owner-fallback refusal:** a console-routed request with no forwarded
  identity is rejected; explicitly assert it does **not** resolve to the owner.
- **Picker is not a grant:** a request naming a profile the caller does not
  administer is refused even when the console offered it.
- **Secret isolation:** two profiles with different DSNs and different
  service-role keys, exercised concurrently, each act on their own database —
  the regression test for the process-global environment problem.
- **Audit:** cross-profile actions land in the target profile's ledger with the
  acting subject.
- Registry: unreachable profile degrades gracefully (switcher marks it down;
  no request is silently routed elsewhere).

## System testing (system-test box)

On `hermes-systest`: create a second profile with its own schema (FG-27), enrol
one admin in both and one admin in only one, and confirm from the console that
the second admin cannot see or act on the profile they are not enrolled in —
including with a hand-crafted request naming that profile directly.

## Dependencies

- **Blocked by:** nothing outstanding. FG-27 (all layers, system-tested, PR #210) and
  FG-26 (system-tested, PR #217) are both **done and deployed**.
- **Related:** C1 (principal), C3 (datastore router), FG-20 (BFF), the kanban
  identity gap below.

## The other side of a shared account system: global accounts, local authority

Sharing one Supabase gives FG-28 its clean entitlement model, but it also means
an **account** is a box-wide object while **authority** is per profile. The
administrative verbs FG-26 treats as one thing are actually two:

| kind | operations | blast radius |
|---|---|---|
| **enrolment-level** | add / remove / re-role the `principals` row, group membership | the acting profile only |
| **account-level** | GoTrue ban (deactivate), delete, set/reset password | **every profile the account is enrolled in** |

`MemberService` performs the account-level operations through the GoTrue admin
API with the **service-role key**, gated by `require_member_admin`, which checks
the actor's role *in the current profile*. So an admin of `hr` can ban an
account that is also enrolled in `engineers` and revoke their access there —
per-profile authority exercised through a globally-scoped credential. The
profile boundary holds perfectly for data and does not hold at all for accounts.

Symmetrically: **every profile's process holds a key that can mint an account
valid in every profile**, so compromising one profile's process is a box-wide
account-system compromise. Fan-out would have meant N processes holding it; the
one-process decision (§"Architecture decision") means one, which is the shape the
"preferred" requirement below already asked for.

Requirements (mirrored in FG-26 §3.5):

- **"Deactivate" in a profile context means un-enrol**, not ban — the correct
  per-profile verb, and the default the UI offers.
- **Account-level operations require owner, or that the target is enrolled
  solely in profiles the actor administers** — checked server-side across
  profiles, with the affected profiles named in the confirmation dialog.
- **Preferred:** account-level operations move behind the control plane and stop
  being reachable from each profile's process, so the service-role key lives in
  exactly one place. This is the strongest argument for the registry service
  being more than a lookup table, and it should be decided before FG-26 ships
  its delete/deactivate UI — otherwise that UI has to be rebuilt.

## Related finding

The shared kanban board carries `owner_user_id` and `visibility` while
`principals` is per profile, so the same `user_id` denotes different people in
different profiles. A shared GoTrue subject namespace — prerequisite (1) above —
would incidentally close that ambiguity, which is a second argument for it.

## Definition of Done

Registry + one console with a switcher scoped to the caller's administered
profiles; every console request scoped to its target profile with the principal
re-resolved there and owner-fallback refused;
FG-26 create-user picker; audit in the target profile; full negative matrix and
secret-isolation tests green on real Postgres; `scripts/run_tests.sh`, `ruff`,
`ty` clean; system test passed.

## Progress checklist

- [x] **First:** verify or refute the multiplexed-gateway `os.environ` issue; file separately if real — **confirmed on three paths and fixed** (see §"Verified 2026-08-12"), and the subprocess seam closed with it (see §"The subprocess seam")
- [ ] Decision recorded: single shared GoTrue across profiles (yes/no)
- [ ] Profile registry at the shared root + CLI
- [ ] Credential reads on console-reachable paths migrated to `get_secret()`
- [ ] One console + profile switcher scoped to administered profiles, each request scoped to its target profile
- [ ] Identity carried into the target scope + owner-fallback refusal on console routes (with negative tests)
- [ ] FG-26 create-user picker over administered profiles
- [ ] Cross-profile audit in the target profile's ledger
- [ ] Negative matrix + secret-isolation tests on real Postgres
- [ ] System test on `hermes-systest` passed

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-13 | 5 | devin (for Leo) | Architecture resolved: **one process, profile-scoped per request**; fan-out demoted to a recorded rejected alternative | Leo read the doc and found the contradiction edition 4 introduced: the body recommended fan-out because “the process boundary is what keeps secrets apart” while the new pickup prompt said in-process multiplexing was the direction. Resolved in favour of one process, on three grounds. **(1)** Fan-out's argument was load-bearing on a premise that is no longer true — `set_secret_scope` is context-local and `get_secret` fails closed, and #219/#220 extended that to a spawned child's environment. **(2)** Fan-out is *incompatible* with the one-gateway consolidation this same FG carries: with `multiplex_profiles` on, port-binding platforms are a hard startup error for a secondary profile and the default profile serves the rest under `/p/<profile>/`, so the per-profile HTTP endpoints a console would fan out to stop existing. The doc was asking for two mutually exclusive runtimes. **(3)** “Keep one process per profile, exactly as today” was factually wrong for this tier — the box runs exactly one `hermes-dashboard` unit on one `HERMES_HOME`, which is precisely why FG-26's picker can only see the current profile; fan-out would have been N new units at ~225 MB, not the status quo. Recorded what the decision *costs*, because fan-out's real prize was never the secrets: authority re-derived at the destination stops being a process boundary and becomes a discipline, so the doc now requires a single scoping chokepoint, the principal re-resolved from the target profile's own `principals`, owner-fallback refused there, FG-27's schema-ownership guard as the loud backstop, and the credential migration done *before* a second profile is served — with the two-profiles-one-process secret-isolation test promoted to load-bearing. |
| 2026-08-13 | 4 | devin (for Leo) | Item 1 recorded as closed; the cloud-agent prompt rewritten for a cold pickup | Leo asked whether another agent could pick this FG up from the repo alone. The code and the findings were committed and pushed, but the prompt would have misdirected the reader on four counts: it gated the FG behind FG-27 (done and deployed), it opened with a Task 0 that is now answered (the `os.environ` leaks, confirmed on four paths and fixed in #219/#220), it still carried the retired "users belong to exactly one profile" premise, and — most consequentially — it instructed the reader to keep one process per profile **because no context-local seam for `os.environ` existed**. That seam now exists, which inverts the architectural instruction while leaving its underlying reason intact: 6 of ~2,250 env reads are migrated, and an unmigrated `os.getenv` returns the wrong profile's value silently. The prompt now states the four settled decisions (shared GoTrue with box-wide accounts and profile-local authority, FG-25 deferred, Leo's owner/admin-picks-the-profile rule, and the closed leak investigation), points at the reframing that supersedes §"Summary", and names what a cloud agent cannot do — no SSH to `hermes-systest`, so deployment and the live system test stay with the box operator. |
| 2026-08-10 | 3 | devin (for Leo) | Reframed as the goal-tree console; one-gateway-for-all-profiles brought into scope | Leo's domain model: a **profile is the instrument for one sub-goal**, not a tenant and not a container of people, and **people participate in as many profiles as their work spans**. That retires this FG's "users belong to exactly one profile" premise — an imported constraint, not one the system imposes, since one shared GoTrue subject can hold a `principals` row in several profiles with separate memory in each — and it retires FG-25 for v1, because profiles now carry the cohort structure that hierarchical groups were designed to express. The mechanics below (authority via the `principals` row, target-profile routing, owner-fallback refusal, account-vs-enrolment split) are unchanged; only the motivation is. **Also brought one gateway per box into scope**, at Leo's request and on measured evidence: a per-profile daemon is 150 MB resident before any conversation (plus ~225 MB for a per-profile console), so ten sub-goal profiles cost ~3.7 GB idle against 9.6 GB available. Reading the code corrected an earlier claim of mine: `agent/secret_scope.py` **already solves** the process-global-environment problem for the gateway path with a context-local, fail-closed secret scope that never mutates `os.environ`, alongside same-token collision detection and a shared listener with `/p/<profile>/` routing. The remaining work is finishing the migration, not building it — and the risk is precisely asymmetric: an unmigrated `get_secret()` caller raises, while an unmigrated `os.getenv` caller silently returns the wrong profile's value, with only 6 of ~2,250 env reads migrated so far. |
| 2026-08-10 | 2 | devin (for Leo) | Shared-Supabase decision resolved; account-vs-enrolment authority split added | Leo confirmed **all profiles share one Supabase instance**, closing prerequisite (1): one GoTrue, one subject namespace, so the no-new-tables entitlement model holds and the kanban identity ambiguity closes incidentally. Two corrections follow. **(a) The `os.environ` finding is weaker than written** — with one Supabase, `DATABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are the *same value* in every profile, so the process-global environment cannot make them wrong and in-process multiplexing is feasible for the datastore. What survives is per-profile secrets that genuinely differ (model API keys) and the fact that the property holds only by coincidence — nothing declares or enforces that the values must match, so the day one profile gets its own key the multiplexed process silently uses the wrong one. Fan-out is therefore downgraded from hard blocker to strong recommendation, with an explicit fail-closed assertion required if multiplexing is chosen instead. **(b) A new hole, and the sharper one:** the account is now box-wide while authority stays per profile. `MemberService` performs ban/delete/reset through the GoTrue admin API with the shared service-role key, gated only by `require_member_admin` against the *current* profile — so an `hr` admin can ban an account enrolled in `engineers` and revoke access there. Split the verbs: "deactivate" in a profile means **un-enrol**, and account-level operations need owner or a target enrolled solely in profiles the actor administers. Symmetrically, every profile process holding that key means one compromised process is a box-wide account compromise, which argues for account operations living behind the control plane. Also promoted FG-27 Layer 3 to an absolute prerequisite: one Supabase means every profile shares a DSN, so without profile-derived schemas the second profile merges into the first on contact. |
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo asked for one admin to create users in several profiles (CTO → engineers+testers, CFO → hr) while users stay single-profile. Code reading found the entitlement model needs **no new tables** — the GoTrue `sub` is already `principal.user_id`, so a per-profile `principals` row *is* the per-profile grant, and `_comms_resolve_principal` already 409s for an authenticated-but-unenrolled subject. It also found the architectural constraint: `HERMES_HOME` is a contextvar but `os.environ` is not, and `dsn: ${DATABASE_URL}` resolves through `_expand_env_vars` reading `os.environ`, with ~2,250 env call sites and no context-local seam — so a single process cannot hold per-profile secrets, and the console must fan out to per-profile processes rather than multiplex in-process. Recorded the owner-fallback in `_comms_resolve_principal` as the most dangerous hole: correct today, an escalation to *the target profile's owner* the moment a sessionless service-to-service hop is introduced. Sequenced after FG-27 Layers 1+3 because per-profile schemas turn a wrong-DSN resolution from a silent merge into a detectable error. |

## Cloud-agent prompt

> **[Start here. Nothing gates this FG any more: FG-26 and FG-27 are done,
> deployed and system-tested, and item 1 of this FG's checklist is closed.]**
> Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read
> `docs/design/master-plan/README.md`, `AGENTS.md`, FG-26, FG-27, FG-29 and this
> doc — including §"Reframing", which supersedes the motivation in §"Summary".
> Goal: one admin console at one URL where an administrator manages users in
> **several** profiles, scoped to the profiles where they hold an `admin`/`owner`
> principal row.
>
> **Four things are already settled — do not reopen them.**
>
> 1. **All profiles share ONE Supabase instance**, so one GoTrue and one
>    `auth.users`. An **account is box-wide; enrolment and authority are
>    profile-local**, and the same person legitimately holds `principals` rows in
>    several profiles. The "users belong to exactly one profile" line in
>    §"Summary" was an imported constraint and is retired.
> 2. **FG-25 (hierarchical groups) is deferred.** Profiles carry the cohort
>    structure. Build no groups UI, no group CRUD, no elevation ledger.
> 3. **Leo's decision on profile assignment:** owner/admin picks the profile when
>    creating a user — never self-selection, never a default-profile fallback.
>    FG-26 shipped the picker limited to the current profile *because* this FG is
>    what makes cross-profile writes possible; widening it is your item.
> 4. **The `os.environ` question is answered and closed** (was Task 0). It was
>    real, on four paths, fixed in #219 and #220 — see §"Verified 2026-08-12" and
>    §"The subprocess seam". Do not re-probe it; do **read** it, because it is
>    the mechanism your architecture rests on.
>
> **Architecture — read §"Architecture decision (2026-08-13)" before writing
> anything; it is the decision, and §"Recommended architecture" as it stood in
> editions 1–3 is now the *rejected* alternative recorded beneath it.** Build **one
> process serving every profile, entering the target profile's scope per
> request** (`set_hermes_home_override` + `set_secret_scope` at one chokepoint),
> plus a control-plane **profile registry** at the shared Hermes root (name, base
> URL, health; **no authority data**). Fan-out to per-profile APIs is not an
> option you may re-pick unilaterally: it is incompatible with the one-gateway
> consolidation this FG also carries, since with `multiplex_profiles` on the
> port-binding platforms are a hard startup error for a secondary profile and the
> per-profile HTTP endpoints stop existing.
>
> **Two things the decision costs you, and they are your first two tasks.**
> (a) Only 6 of ~2,250 env reads go through `get_secret`, and an unmigrated
> `os.getenv` returns the wrong profile's value **silently** while `get_secret`
> raises — so migrate every credential read a console route can reach *before* you
> serve a second profile from one process, and assert it with a real test (below),
> never by inspection. (b) The process boundary was what made "authority is
> re-derived at the destination" structural; in one process you must re-resolve the
> principal inside the target profile's scope from that profile's own `principals`
> table, refuse owner-fallback there, and let FG-27's schema-ownership guard be the
> backstop that makes a scoping bug loud.
>
> **Authority model: add no new tables.** The GoTrue `sub` is already
> `principal.user_id`, so "CTO may administer engineers" means exactly "CTO has
> an `admin` row in engineers' `principals`". Absence of a row is absence of
> authority. The registry holds **no** authority data.
>
> **The critical security requirement.** `_comms_resolve_principal` currently
> falls back to the enrolled **owner** for a request with no interactive
> session. On a console-routed request that is an escalation to the target
> profile's owner. Verify the caller's original GoTrue access token — the same JWT
> a browser would present — inside the target profile's scope, and **refuse
> owner-fallback on console-routed requests**: no verified subject is 401, never
> owner. In one process this is more dangerous than it was under fan-out, because
> the hop looks like a function call rather than a service call. Add a negative
> test that strips the identity and asserts failure rather than fallback. The
> profile picker is a routing hint, never a grant: re-derive authority inside the
> target profile's scope and 409/403 when the caller has no row there, with a test
> that tries it.
>
> **Also:** turn FG-26's read-only "creating in: X" label into a picker over the
> caller's administered profiles; audit every cross-profile administrative
> action (C5) in the **target** profile's ledger.
>
> **Tests (real Postgres, not mocks):** the full negative matrix on the console
> routes; owner-fallback refusal; picker-is-not-a-grant; and a secret-isolation
> test with two profiles on different DSNs and different service-role keys served
> concurrently **by one process** — which is now the load-bearing test, not a
> nice-to-have, since it is what the process boundary used to guarantee for free.
> Then the `hermes-systest` procedure in this doc. `scripts/run_tests.sh`, `ruff`,
> `ty` clean.
>
> **What you cannot do, and must hand back.** You have no SSH path to
> `hermes-systest` and no credentials for it, so the deployment and the live
> system test stay with the box operator. Everything else — including the real-
> Postgres tests — runs locally. Note also that `tests/agent` makes **real**
> provider calls, so run focused selections rather than the whole tree, and
> compare any broad failure set against `develop` before attributing it to your
> branch.
