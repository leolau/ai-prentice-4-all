# FG-27 — Profile-scoped app-layer datastore isolation (close the shared-schema footgun)

**Wave:** P6-0 (Phase-6 **prerequisite** — lands before FG-25/FG-26 touch app tables) · **Owner agent:** _unassigned_ · **Status:** PLAN — not started

## Summary

A Hermes **profile** is an isolated brain: its own `HERMES_HOME`, `config.yaml`,
`.env`, memory files, skills, and `state.db`. Everything keyed on
`$HERMES_HOME` is isolated **by construction** — two profiles physically cannot
share it.

The **Supabase app layer is not**. In `hermes_cli/datastore.py`:

```python
dsn    = config["datastore"]["supabase_app"]["dsn"]   # from THIS profile's config.yaml
schema = "app_dev" if mode == "dev" else "app_prod"   # HARD-CODED, identical in every profile
return SupabaseAppStore(resolved_mode, schema, dsn)   # server_settings={"search_path": schema}
```

A profile's app data is addressed by `(dsn, "app_prod")`, and the **only**
discriminator is a DSN string in a YAML file. Two profiles pointed at the same
database silently share **one** `principals`, `memories`, `memory_projection`,
`changes`, `interactions` and `item_grants`. Enrol a user in `engineers` and
they appear in `testers`.

**RLS does not save you here.** It is scoping rows correctly — inside a database
that both profiles believe is exclusively theirs. The boundary that fails is
*above* RLS.

This FG makes app-layer isolation structural rather than conventional.

## Why this is a prerequisite, not a cleanup

Three reasons it must land before the rest of Phase 6:

1. **FG-25 and FG-26 add identity-bearing tables** (`groups`, `group_members`,
   `invitations`). Adding them to a schema that two profiles may share means an
   invitation minted in one profile is redeemable in another, and a group
   created for one org is visible to a different one. The blast radius of the
   footgun grows with exactly the tables Phase 6 adds.
2. **The `--clone` path makes it the default outcome** (below).
3. **The multiplexed gateway already runs several profiles in one process**
   (`gateway.multiplex_profiles`), so the collision no longer needs two machines
   or two operators to happen — it can happen inside one process, one turn apart.

## The failure, precisely

### It fires on the documented happy path

`hermes profile create testers --clone` — the recommended "start from my
default" path — copies `config.yaml` verbatim:

```python
_CLONE_CONFIG_FILES = ["config.yaml", ".env", "SOUL.md"]
```

Nothing in `hermes_cli/profiles.py` references `dsn`. There is no prompt, no
rewrite, no warning. The new profile inherits the source's DSN and therefore
resolves to **exactly the same `(dsn, app_prod)`**. The user has followed the
documentation and produced two "isolated" profiles sharing one database.

### Nothing detects it

- Two processes on one schema is what a *legitimate* single profile looks like
  (gateway + web server + CLI all connect concurrently). There is no signal to
  distinguish "my other process" from "a different profile".
- `initialize_supabase_app()` is `CREATE SCHEMA/TABLE IF NOT EXISTS`, so the
  second profile finds everything present and proceeds happily.
- No log line, no error, no health check.

### It is invisible on disk

`state.db`, `memories/`, `config.yaml`, `skills/` are all genuinely separate.
Every surface an operator would inspect says "isolated". Only the Postgres side
is shared — which is why this survives review.

### Discovery is late and recovery is manual

The symptom appears as *"why can this user see the other team's data"*, at which
point both profiles' rows are interleaved in the same tables with no column
recording which profile wrote them. Separating them afterwards is a hand
reconstruction.

## Decisions applied

- **D1 — one brain per profile.** This FG enforces at the datastore layer what
  D1 already asserts architecturally.
- **D4 / C3 — the datastore router is the single chokepoint.** The fix belongs
  in `get_store()`, not in each caller.
- **Fail closed** (master-plan principle 2). A profile that cannot prove the
  schema is its own must refuse to start, not proceed.
- **Profiles are independent islands on purpose.** `AGENTS.md` states this
  explicitly and cites a closed PR that added cross-profile config inheritance:
  "coupling profiles together is exactly what the design prevents." This FG is
  the *conservative* direction — it enforces the stated intent rather than
  widening it, which is why it needs no new contract and should be
  uncontroversial to merge.

## The shared-Supabase decision changes this FG's centre of gravity

**Decided 2026-08-10: all profiles share one Supabase instance.** That single
fact rewrites two of the three layers.

One Supabase means one Postgres, which means **every profile has the same DSN by
design**. Consequences:

- **The collision is no longer a footgun — it is the guaranteed outcome.** With
  a shared DSN and a hard-coded `app_prod`, the *second* profile merges into the
  first the moment it connects. There is no configuration that avoids it.
- **Layer 3 is therefore not "the real fix", it is the enabling mechanism.**
  Profile-derived schemas are the only thing that makes more than one profile
  possible on this deployment at all. It moves from "do it eventually" to
  "nothing multi-profile can ship before it".
- **Layer 2 as originally written is now wrong.** Blanking the app DSN on
  `--clone` would break the intended topology: profiles are *supposed* to share
  the database. Layer 2 is re-scoped from **"don't share the database"** to
  **"share the database, never the schema"** — the clone must ensure a distinct
  resolved schema, and warn only when it cannot derive one.
- **Layer 1 is unaffected.** The marker keys on the schema, not the DSN, so
  claim-and-verify works identically when every profile shares a database.

Revised build order: **Layer 3 → Layer 1 → Layer 2**. Layer 3 first because
nothing works without it; Layer 1 second as the fail-closed backstop.

## Design / approach — three layers

**Original build order (pre-shared-Supabase): Layer 1 → Layer 3 → Layer 2.**
See above — with one shared Supabase this becomes **3 → 1 → 2**. The reasoning
below for preferring outcome-checks over input-policing is unchanged and is what
makes Layer 3 the right thing to lead with.

The layers are numbered by the order they were discovered, not the order they
should be built. Layers 1 and 3 are the safety controls; Layer 2 is UX.

The reason is a general one. **Layer 2 polices an input path; Layers 1 and 3
check the resolved outcome.** A DSN can arrive as a literal in `config.yaml`,
as `${DATABASE_URL}` from `.env` (what the live box actually does), from the
process environment, or from a per-mode `datastore.overrides.<mode>` entry —
and any path not enumerated is a hole. Layer 3 makes the enumeration
unnecessary: with `app_prod_<profile>`, two profiles sharing a DSN is simply
**harmless** — same database, different schemas, isolated by construction.
Layer 1 then catches whatever Layer 3 misses, at connect time, where the truth
is unambiguous.

So Layer 2 remains worth doing — inheriting a database silently is bad
ergonomics even when it is safe — but it is **not** the thing standing between
you and a data merge, and it should not be built as though it were.

### Layer 1 (build second under shared Supabase) — collision detection, fail-closed

Write an ownership marker into the schema at initialisation and verify it on
every connect:

```sql
CREATE TABLE IF NOT EXISTS <schema>.schema_owner (
  singleton    BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
  profile_name TEXT NOT NULL,
  hermes_home  TEXT NOT NULL,
  claimed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- `initialize_supabase_app()` claims the schema for the active profile
  (`get_active_profile_name()` — already exists) via an
  `INSERT … ON CONFLICT DO NOTHING`.
- `SupabaseAppStore.connect()` verifies the marker matches. A mismatch is a
  **hard startup error** naming both profiles and telling the operator to give
  the new profile its own database or run the FG-27 migration.
- **Adoption path for existing deployments:** an unmarked schema is claimed by
  the first profile that connects (so today's single-profile installs are
  unaffected and silent), and `hermes doctor` reports the claim.
- `profile_name` alone is a weak key (two boxes can both have `default`), so the
  marker also records `hermes_home` and the check compares both. A deliberate
  re-claim is `hermes datastore claim --force`, which is audited (C5).

This converts a silent data merge into an error at first connection. It does
**not** make sharing safe — it makes it impossible to do by accident.

### Layer 2 (build last — UX, not a safety control) — stop `--clone` from arming it

`hermes profile create <name> --clone` must handle the inherited DSN explicitly.
Options, in preference order:

**Under the shared-Supabase decision the goal is not to stop the clone sharing a
DSN** — it is supposed to. The clone must instead guarantee the new profile
resolves to a **distinct schema**, which Layer 3 gives it automatically from the
profile name; Layer 2's remaining job is to *verify* that, print the resolved
`(database, schema)` pair so the operator can see it, and refuse when the clone
would land on a schema that is already claimed. Same for `--clone-all` and
`hermes profile import`.

The original framing is kept below because it remains correct for deployments
that give each profile its own database:

1. **Blank the app DSN in the cloned profile** and print what to set;
2. or keep it and **print a prominent warning** that the clone shares a database
   until changed.

In that topology (1) is right: a profile with no app DSN degrades to core-only
(SQLite) rather than silently sharing, which is the fail-closed direction.

**The live deployment shows the DSN is indirect, which makes this worse than it
looks.** On `hermes-systest`, `config.yaml` contains:

```yaml
datastore:
  supabase_app:
    dsn: ${DATABASE_URL}          # interpolated from .env
```

with the real connection string in `$HERMES_HOME/.env`. Two consequences:

- `_CLONE_CONFIG_FILES` copies **both** `config.yaml` *and* `.env`, so the clone
  inherits the interpolation **and** the value — blanking only `config.yaml`
  would not be enough.
- An operator inspecting the cloned `config.yaml` to "point it at its own
  database" sees `${DATABASE_URL}`, not a DSN. There is nothing there to edit,
  so the natural reaction is to assume it is already correct. The indirection
  hides the very field that needs changing.

Layer 2 must therefore treat the **resolved** DSN, not the literal config value:
ask the config layer what `dsn` evaluates to (the same resolution `get_store()`
performs), act on wherever the value actually came from — blanking
`DATABASE_URL` in the cloned `.env` while leaving the `${DATABASE_URL}`
reference in `config.yaml` intact, so the operator sees the shape they are meant
to fill in — and print the resolved target (`host:port/db`, credentials
redacted) so the inheritance is visible.

Comparisons must also be on **resolved** DSNs, not strings: two profiles can
share a database while their config text differs. On the live box
`127.0.0.1:5432/postgres` and `172.18.0.4:5432/postgres` are the same Postgres
reached by two routes — a string comparison would call them distinct.

This fragility is precisely why Layer 2 is sequenced last and classified as
ergonomics: a control whose correctness depends on enumerating every way a
value can reach the process is the weaker kind.

### Layer 3 (build FIRST under shared Supabase — the enabling mechanism) — namespace the schema by profile

Schema becomes profile-derived rather than global:

```
app_dev_<profile>  /  app_prod_<profile>      (default profile keeps app_dev / app_prod)
```

- `get_store()` resolves the profile via `get_active_profile_name()`, which
  already honours the context-local `HERMES_HOME` override — so a multiplexed
  gateway turn scoped with `set_hermes_home_override()` automatically selects
  the right schema **with no caller changes**. This is the property that makes
  the fix cheap.
- The profile name is validated against the existing `_PROFILE_ID_RE` and the
  resulting schema against `_VALID_SCHEMA` before it reaches SQL.
- The default profile **keeps the current names**, so existing single-profile
  deployments need no migration and the baseline is byte-identical.
- Removes the entire class of error: two profiles on one DSN can no longer
  collide, because their addresses differ by construction — **regardless of how
  the DSN was resolved**, which is why this outranks Layer 2.

**Explicit config override.** `datastore.supabase_app.schema` may pin a schema
when an operator genuinely wants two profiles to share (a legitimate case: a
staging profile reading production data read-only). Sharing then requires
writing it down, which is the difference between a decision and an accident.

**Migration for deployments that already share.** Provide
`hermes datastore split-profile <name>` — create the new schema, copy the rows
that belong to it, and verify counts — but the honest answer is that once rows
are interleaved with no provenance column, **the split cannot be automated**;
the tool can only move a whole schema, not disentangle two profiles' rows. This
is precisely why prevention (Layers 1 and 3) matters more than migration: there
is no reliable cure after the fact.

## Data model

New: `<schema>.schema_owner` (one row). Altered: none. The Layer-3 change is a
naming change, not a shape change — every table keeps its definition, RLS policy
and indexes.

## Non-goals

- **Not** cross-profile identity or a shared user directory (that is the
  separate multi-profile-administration FG).
- **Not** a change to SQLite core storage — it is already isolated.
- **Not** a change to the kanban board, which is shared across profiles
  **deliberately** (`hermes_cli/kanban_db.py`: "profiles intentionally collapse
  onto a shared board: it IS the cross-profile coordination primitive").
  See the related risk below.

## Related finding (recorded, not fixed here)

The shared kanban board carries `owner_user_id` and `visibility`
(`shared` / `private:<user>`) — C2's vocabulary — but `principals` is
**per profile**. So the same `user_id` on a shared board denotes different
people in different profiles, and nothing reconciles them. Harmless today
(effectively one human uses it); it becomes a real ambiguity as soon as several
multi-user profiles share a board. Fixing it needs a box-level identity
namespace, which belongs with the multi-profile administration FG, not here.

## Current deployment status (checked 2026-08-10)

`hermes-systest` has **one** profile (`HERMES_HOME=/opt/data/hermes-home-staging`,
no `profiles/` directory, a single `config.yaml`). **The footgun has not fired**
— there is nothing to disentangle, and this FG is purely preventative today.
That is the good case and the reason to land it before a second profile exists.

## Testing requirements

- **Collision detection:** profile B connecting to a schema claimed by profile A
  raises at connect, with both names in the message; identical `(profile,
  hermes_home)` reconnects cleanly; an unmarked schema is adopted silently.
- **Fail-closed:** a marker read failure refuses the connection rather than
  proceeding unverified.
- **Clone:** `--clone` / `--clone-all` / `import` do not produce a profile that
  silently shares a DSN+schema; asserted on the **resolved** DSN of the new
  profile, including the `dsn: ${DATABASE_URL}` indirection case where the value
  lives in the copied `.env` rather than in `config.yaml`.
- **Schema derivation:** `default` ⇒ `app_prod` / `app_dev` (**baseline,
  byte-identical**); a named profile ⇒ `app_prod_<name>`; invalid names
  rejected before reaching SQL; the explicit `schema` override wins.
- **Multiplex:** under `set_hermes_home_override()`, two turns for two profiles
  in **one process** resolve to different schemas — and, with an intentionally
  shared DSN, prove a row written in one is invisible in the other (real
  Postgres).
- **RLS survives renaming:** `apply_scope_rls` / `apply_item_grants_rls` install
  correctly against a namespaced schema; the FG-25 negative-access matrix passes
  unchanged under a named profile.
- **Migration:** `split-profile` moves a whole schema and verifies row counts;
  refuses (with a clear explanation) when asked to disentangle interleaved rows.
- `hermes doctor` reports schema, DSN host and claiming profile.

## System testing (system-test box)

On `hermes-systest`: create a second profile with `--clone`, confirm it does
**not** silently share `app_prod`; deliberately point it at the same DSN and
confirm the startup error; then give it its own schema and confirm both profiles
run concurrently with fully disjoint `principals` and memories.

## Dependencies

- **Blocked by:** none (C3/FG-13 already merged).
- **Blocks:** FG-25 and FG-26 **should not add identity tables until Layers 3
  and 1 land** — those layers are days of work, so this is a sequencing note,
  not a schedule risk.
- **Related:** FG-13 (C3 router), FG-20 (agent-home reads the same schema),
  gateway multiplexing.

## Definition of Done

`schema_owner` claim + verify with fail-closed behaviour and a clear error;
`--clone`/`--clone-all`/`import` no longer arm the collision;
profile-derived schema names with the default profile byte-identical to today;
explicit `schema` override supported; `split-profile` migration + honest refusal
for interleaved data; `hermes doctor` surfaces the binding; FG-25's negative
matrix green under a named profile; `scripts/run_tests.sh`, `ruff`, `ty` clean;
system test passed.

## Progress checklist

- [x] Layer 3 (**first** — enabling mechanism on a shared Supabase) — profile-derived schema in `get_store()`, default profile unchanged, name validation *(explicit `datastore.supabase_app.schema` override deferred — see deviations)*
- [x] Layer 1 (**second**) — `schema_owner` table, claim on init, verify on connect, fail-closed *(`claim_schema_owner(force=True)` exists; the `hermes datastore claim --force` CLI is deferred)*
- [ ] Layer 2 (**last, UX**) — `--clone` / `--clone-all` / `import` verify the new profile resolves to a **distinct schema** on the shared database, print the resolved `(database, schema)`, refuse an already-claimed schema
- [ ] `hermes datastore split-profile` + `hermes doctor` reporting
- [x] Tests: derivation baseline, collision, fail-closed, two-profile isolation on real Postgres (`tests/hermes_cli/test_fg27_profile_schema_isolation.py`, `tests/hermes_cli/test_fg27_schema_isolation_e2e.py`)
- [ ] Tests: clone, RLS-after-rename, migration
- [ ] System test on `hermes-systest` passed

## Deviations from this spec, as built (Layers 3 + 1)

Three, all recorded rather than silently absorbed:

1. **The marker column is `profile_slug`, not `profile_name`.** The slug is what
   actually determines the schema (it is identifier-safe and collision-hashed),
   so storing the raw name would let the marker and the schema disagree.
2. **A `hermes_home` mismatch warns; only a `profile_slug` mismatch fails
   closed.** The spec had the check compare both. Same profile name from a
   *different* home is far more often a relocated deployment than a second box
   sharing one DSN, and hard-failing that would brick a working install for the
   rarer case — so it is a loud warning naming both homes, while the slug
   remains the fail-closed rule.
3. **The explicit `datastore.supabase_app.schema` override is deferred.** A
   pinned schema and `initialize_supabase_app()` disagree about what to create
   (initialisation resolves both modes' schemas from the profile and has no
   store to consult), so a half-wired pin would point a store at tables nobody
   created. It is an escape hatch, not part of the isolation guarantee.

Also still open after this slice: **`agent-home` resolves the schema in
TypeScript** (`schemaForMode()` in `agent-home/src/lib/env.ts` returns the
literal `app_dev`/`app_prod`), so the console reads the *default* profile's
schema whatever profile it is looking at. Harmless while the console is
single-profile; it must be fixed by FG-26/FG-28, which own the console.

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-11 | 5 | devin (for Leo) | Layers 3 + 1 implemented | Schema resolution moved behind `app_schema(mode)`, which derives `app_prod_<profile>` from `get_active_profile_name()` (default profile byte-identical). The surprise was scale: the schema was hard-coded not only in `get_store()` but in **61 SQL literals** across `promote.py`, `tools_registry.py`, `changes.py`, `access.py`, `interactions.py` and a `SupabaseAppStore("prod", "app_prod", dsn)` in `gateway/run.py` — schema-qualified SQL that bypasses the connection's `search_path`, so changing the router alone would have left every one of those writing into the default profile's schema. All now resolve through `app_schema()`. Layer 1 lands as `schema_owner` claimed during `initialize_supabase_app()` and verified inside `SupabaseAppStore.connect()`, with success cached per `(dsn, schema, slug)` and failure never cached. Deviations recorded above. |
| 2026-08-10 | 4 | devin (for Leo) | Shared-Supabase decision — build order becomes 3 → 1 → 2, Layer 2 re-scoped | Leo confirmed **all profiles share one Supabase instance**. One Supabase is one Postgres, so every profile has the **same DSN by design** and the collision stops being a footgun: with a hard-coded `app_prod` the second profile merges into the first the moment it connects, and no configuration avoids it. Layer 3 is therefore promoted from "the real fix" to **the enabling mechanism** — profile-derived schemas are the only thing that makes more than one profile possible on this deployment — and it must be built first. Layer 1 is unaffected (its marker keys on the schema, not the DSN) and becomes the fail-closed backstop. Layer 2 as written is now **wrong**: blanking the cloned DSN would break the intended topology, so it is re-scoped from "don't share the database" to "share the database, never the schema" — verify the clone resolves to a distinct schema, print the resolved `(database, schema)`, refuse an already-claimed one. The original framing is retained for deployments that give each profile its own database. |
| 2026-08-10 | 3 | devin (for Leo) | Reordered build sequence to 1 → 3 → 2 | The `${DATABASE_URL}` indirection found on the live box showed Layer 2 to be the weaker kind of control: it polices an *input path*, and a DSN can arrive as a config literal, an `.env` interpolation, a process env var, or a per-mode override — any path not enumerated is a hole. Layers 1 and 3 check the *resolved outcome* instead. Layer 3 in particular makes the enumeration unnecessary, because with `app_prod_<profile>` two profiles sharing a DSN is harmless rather than catastrophic. Layer 2 is retained as ergonomics (silently inheriting a database is still bad UX) but is no longer what stands between the operator and a data merge, and the FG-25/FG-26 sequencing gate now reads "Layers 1 and 3" rather than "Layers 1 and 2". Approved by Leo. |
| 2026-08-10 | 2 | devin (for Leo) | Live-deployment check + DSN indirection | Ran the collision check on `hermes-systest`: one profile only, no `profiles/` directory, single `config.yaml` — **the footgun has not fired**, so this FG is purely preventative today. The check also surfaced that the DSN is *indirect* (`dsn: ${DATABASE_URL}` in `config.yaml`, real value in `$HERMES_HOME/.env`), which makes Layer 2 harder than written: `_CLONE_CONFIG_FILES` copies both files, so blanking `config.yaml` alone would not break the inheritance, and an operator opening the cloned `config.yaml` to repoint it sees `${DATABASE_URL}` rather than a connection string — the indirection hides the field that needs changing. Layer 2 must act on the resolved DSN. |
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Found while scoping multi-profile administration: `get_store()` hard-codes the app schema to `app_dev`/`app_prod`, so profile isolation at the app layer rests entirely on each profile's `config.yaml` carrying a distinct DSN — and `hermes profile create --clone`, the documented "start from my default" path, copies `config.yaml` verbatim (`_CLONE_CONFIG_FILES`) with no mention of `dsn`. Two profiles then share one `principals`/`memories`/`changes` set with no error, no log line, and no on-disk symptom (SQLite, memory files and config are genuinely separate). RLS does not help: it scopes rows correctly inside a database both profiles treat as their own. Chose prevention over cure because interleaved rows carry no provenance column and cannot be disentangled automatically. Sequenced ahead of FG-25/FG-26 because those add exactly the identity-bearing tables (`groups`, `invitations`) whose cross-profile leakage would be most damaging. |

## Cloud-agent prompt

> **[Phase-6 prerequisite — land Layers 3+1 before FG-25/FG-26 add tables. NOTE: all profiles share one Supabase instance, so build order is 3 → 1 → 2 and Layer 3 is the enabling mechanism, not an optimisation]** Repo
> `leolau/ai-prentice-4-all`, branch off `develop`. Read
> `docs/design/master-plan/README.md`, `AGENTS.md`, and this doc (FG-27). Close
> the shared-schema footgun in `hermes_cli/datastore.py`, where `get_store()`
> hard-codes the app schema to `app_dev`/`app_prod` so two profiles pointed at
> one DSN silently share `principals`, `memories`, `changes` and everything else.
> **Layer 1:** add a single-row `<schema>.schema_owner(profile_name,
> hermes_home, claimed_at)`, claimed in `initialize_supabase_app()` and
> **verified on every connect**; a mismatch is a hard startup error naming both
> profiles; an **unmarked schema is adopted silently** so existing installs are
> unaffected; a marker read failure refuses the connection (fail-closed);
> `hermes datastore claim --force` re-claims and emits C5. **Layer 2:** make
> `hermes profile create --clone` / `--clone-all` / `import` stop copying the
> app DSN blindly — prefer blanking it (core-only is the fail-closed
> degradation) over merely warning, and note that on the live box `config.yaml`
> holds `dsn: ${DATABASE_URL}` with the real value in the copied `.env`, so you
> must act on the **resolved** DSN, not the literal config value. **Layer 3:** derive the
> schema from the active profile (`app_prod_<profile>`), resolving it through
> `get_active_profile_name()` so a multiplexed gateway turn scoped with
> `set_hermes_home_override()` picks the right schema **with no caller changes**;
> the **default profile must keep `app_dev`/`app_prod` byte-identically**
> (regression-lock it); validate the profile name and the derived schema before
> it reaches SQL; support an explicit `datastore.supabase_app.schema` override
> for deliberate sharing. Add `hermes datastore split-profile` (whole-schema
> move + row-count verification) that **refuses, with a clear explanation**, to
> disentangle already-interleaved rows — there is no provenance column and
> guessing would be worse than failing. Surface schema/DSN-host/claiming-profile
> in `hermes doctor`. Do **not** change SQLite core storage (already isolated)
> and do **not** touch the kanban board (shared across profiles by design).
> Tests per this doc, including two profiles in **one process** under
> `set_hermes_home_override()` proving disjoint data on a deliberately shared
> DSN, and FG-25's negative-access matrix passing under a named profile's
> schema. Run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open
> a PR linking this doc.
