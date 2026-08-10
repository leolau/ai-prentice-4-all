# FG-28 — Multi-profile administration (one admin, several profiles)

**Wave:** P6-C (Phase-6 **follow-on** — after FG-26; requires FG-27 Layers 1+3) · **Owner agent:** _unassigned_ · **Status:** PLAN — not started

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
identity; `hermes_cli/access.py` uses that same UUID as `principal.user_id`. So
if all profiles share **one** GoTrue, one login produces one subject that is
meaningful in every profile — and each profile's own `principals` table decides
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

**Latent issue to verify first (not introduced by this FG).** The gateway
already multiplexes profiles in one process, scoping each turn with
`set_hermes_home_override()`. By the reasoning above, profiles served that way
already share one `os.environ`, so a multiplexed gateway whose profiles have
different `DATABASE_URL`s or API keys resolves them globally rather than
per-profile. This is a **hypothesis from reading the code, not an observed
failure** — the live box has one profile, so it cannot be exhibiting it.
Confirming or refuting it is the **first task** of this FG, because if it is
real it is a bug in shipped code and outranks the feature.

## Recommended architecture — fan-out, not multiplex

Keep **one process per profile**, exactly as today, and put a thin control plane
in front:

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

Why fan-out beats in-process multiplexing here:

- **The process boundary is what keeps secrets apart** — the one thing the
  contextvar cannot do.
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
ones. Under fan-out it becomes an **escalation vector**: the console→profile-API
hop is service-to-service, so if the caller's identity is not forwarded — or is
dropped on one code path, or the token is accepted without a subject — the
request resolves to **that profile's owner**. CFO's misrouted request against
`engineers` would not be denied; it would be executed as the owner of
`engineers`.

Requirements, all of them tests:

- The fan-out **must** forward the caller's verified subject, and the profile
  API must **refuse owner-fallback** on any request arriving from the console —
  a sessionless request on those routes is a 401, never an owner.
- Prefer forwarding the caller's **original GoTrue access token** over a service
  token, so the profile API verifies the same JWT it would verify from a
  browser and the identity cannot be asserted by the middle tier at all.
- Negative test: a console request with the identity header stripped must fail
  closed, not fall back.

This single behaviour is the difference between a scoped console and a root
console with a dropdown.

## Scope

**In:**

- profile registry at the shared root (`hermes profile registry` CRUD, health);
- console fan-out with a profile switcher listing **only** profiles where the
  caller holds an `admin`/`owner` principal row;
- per-profile identity forwarding + owner-fallback refusal on console routes;
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

1. **Do all profiles share one GoTrue?** The whole simplification depends on it.
   If each profile has its own Supabase, a subject from one is meaningless in
   another and FG-28 needs a real shared identity store — a much larger FG.
   **Decide before implementation.**
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

- **Blocked by:** FG-27 Layers 1+3; FG-26 (the console this extends).
- **Related:** C1 (principal), C3 (datastore router), FG-20 (BFF), the kanban
  identity gap below.

## Related finding

The shared kanban board carries `owner_user_id` and `visibility` while
`principals` is per profile, so the same `user_id` denotes different people in
different profiles. A shared GoTrue subject namespace — prerequisite (1) above —
would incidentally close that ambiguity, which is a second argument for it.

## Definition of Done

Registry + fan-out console with a switcher scoped to the caller's administered
profiles; identity forwarded and owner-fallback refused on console routes;
FG-26 create-user picker; audit in the target profile; full negative matrix and
secret-isolation tests green on real Postgres; `scripts/run_tests.sh`, `ruff`,
`ty` clean; system test passed.

## Progress checklist

- [ ] **First:** verify or refute the multiplexed-gateway `os.environ` issue; file separately if real
- [ ] Decision recorded: single shared GoTrue across profiles (yes/no)
- [ ] Profile registry at the shared root + CLI
- [ ] Console fan-out + profile switcher scoped to administered profiles
- [ ] Identity forwarding + owner-fallback refusal on console routes (with negative tests)
- [ ] FG-26 create-user picker over administered profiles
- [ ] Cross-profile audit in the target profile's ledger
- [ ] Negative matrix + secret-isolation tests on real Postgres
- [ ] System test on `hermes-systest` passed

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo asked for one admin to create users in several profiles (CTO → engineers+testers, CFO → hr) while users stay single-profile. Code reading found the entitlement model needs **no new tables** — the GoTrue `sub` is already `principal.user_id`, so a per-profile `principals` row *is* the per-profile grant, and `_comms_resolve_principal` already 409s for an authenticated-but-unenrolled subject. It also found the architectural constraint: `HERMES_HOME` is a contextvar but `os.environ` is not, and `dsn: ${DATABASE_URL}` resolves through `_expand_env_vars` reading `os.environ`, with ~2,250 env call sites and no context-local seam — so a single process cannot hold per-profile secrets, and the console must fan out to per-profile processes rather than multiplex in-process. Recorded the owner-fallback in `_comms_resolve_principal` as the most dangerous hole: correct today, an escalation to *the target profile's owner* the moment a sessionless service-to-service hop is introduced. Sequenced after FG-27 Layers 1+3 because per-profile schemas turn a wrong-DSN resolution from a silent merge into a detectable error. |

## Cloud-agent prompt

> **[Phase-6 follow-on — do not start until FG-27 Layers 1+3 are merged and
> prerequisite (1) below is answered]** Repo `leolau/ai-prentice-4-all`, branch
> off `develop`. Read `docs/design/master-plan/README.md`, `AGENTS.md`, FG-26,
> FG-27 and this doc. Goal: one admin console at one URL where an administrator
> manages users in **several** profiles, scoped to the profiles where they hold
> an `admin`/`owner` principal row, with users still belonging to exactly one
> profile.
>
> **Task 0 (do this first, and report before building anything).** Determine
> whether the multiplexed gateway already mis-resolves per-profile secrets:
> `set_hermes_home_override()` is a contextvar, but `_expand_env_vars` in
> `hermes_cli/config.py` resolves `${DATABASE_URL}` from the process-global
> `os.environ`, and `reload_env()` writes to it. If two profiles served by one
> process can have different `DATABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY`
> values, this is a shipped bug and takes priority over the feature.
>
> **Architecture:** one process per profile (unchanged) plus a control-plane
> **profile registry** at the shared Hermes root — name, base URL, health — and
> a console that fans out to per-profile APIs. Do **not** multiplex profiles
> in-process in the web tier; the process boundary is what keeps per-profile
> secrets apart, and there is no context-local seam for `os.environ` (~2,250
> call sites).
>
> **Authority model: add no new tables.** The GoTrue `sub` is already
> `principal.user_id`, so "CTO may administer engineers" means exactly "CTO has
> an `admin` row in engineers' `principals`". Absence of a row is absence of
> authority. The registry holds **no** authority data.
>
> **The critical security requirement.** `_comms_resolve_principal` currently
> falls back to the enrolled **owner** for a request with no interactive
> session. Under fan-out that is an escalation to the target profile's owner.
> Forward the caller's original GoTrue access token so the profile API verifies
> the same JWT it would from a browser, and **refuse owner-fallback on
> console-routed requests** — sessionless is 401, never owner. Add a negative
> test that strips the identity and asserts failure rather than fallback. The
> profile picker is a routing hint, never a grant: re-derive authority at the
> destination and 409/403 when the caller has no row there.
>
> **Also:** turn FG-26's read-only "creating in: X" label into a picker over the
> caller's administered profiles; audit every cross-profile administrative
> action (C5) in the **target** profile's ledger.
>
> **Tests (real Postgres, not mocks):** the full negative matrix at the profile
> API; owner-fallback refusal; picker-is-not-a-grant; and a secret-isolation
> test with two profiles on different DSNs and different service-role keys
> exercised concurrently. Then the `hermes-systest` procedure in this doc.
> `scripts/run_tests.sh`, `ruff`, `ty` clean.
