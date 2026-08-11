# FG-25 — Group scopes: multi-dimensional, hierarchical audiences + scoped admin (**publishes C10**)

**Wave:** ~~P6-A~~ **DEFERRED — optional, not in v1** · **Owner agent:** _unassigned_ · **Status:** PLAN — deferred 2026-08-10

> **Deferred (2026-08-10).** The domain model settled in FG-29 makes a
> **profile the instrument for one sub-goal**, and a **person a participant in
> as many profiles as their work spans** — one shared GoTrue subject holding a
> `principals` row in each, with separate memory in each. Departments, classes,
> cohorts and projects are therefore expressed by *profiles*, not by groups, and
> the multi-cohort case that motivated this FG (a teacher on two classes, an
> engineer on two projects) is already supported with no new machinery.
>
> This was the most expensive item in Phase 6 (2–3 sessions, mostly the RLS
> negative-access matrix) and it bought isolation **by policy** where profiles
> give isolation **by construction** — a worse trade for a system whose whole
> value rests on not leaking one cohort's data into another's.
>
> Groups remain the right answer for scoping *within* one large profile (P5 vs
> P6 inside a single teaching instrument). Revisit when a real deployment needs
> that; nothing here is discarded, and C10 stays reserved.

## Summary

C2 today has exactly two audiences — `shared` (everyone) and `private:<user_id>`
(one person) — plus per-item grants (FG-19) for the one-item-to-one-person case.
There is **no way to express a set of people**: "back-end engineers", "class 3B",
"Project Apollo". There is also no scoped authority: `role_reads` (FG-21 P3) is
an instance-wide switch, so an `admin` either outranks *every* member everywhere
or nobody.

This FG adds a **group tier** to C2: `visibility ∈ {shared, group:<group_id>,
private:<user_id>}`, where groups are **hierarchical** (a group has a parent)
and **multi-dimensional** (independent forests, one per organisational axis),
and where **group-scoped admins** replace the instance-wide elevation switch.

It is deliberately an **extension of C2, not a second access system**: one extra
`OR` clause in `scope_filter()` and its `apply_scope_rls()` mirror, exactly as
FG-19's grant clause and FG-21's elevation clause were added.

## Motivation / sizing

The system must serve two shapes with the same primitives:

- a school: profile `students`, ~500 principals, one `cohort` dimension one
  level deep (`3A`, `3B`, …), teachers as group admins;
- a medium business: profile `engineering`, ~500 principals, a deep `org`
  dimension (`eng → backend → payments`) crossed with a flat `project`
  dimension (`apollo`), leads as group admins of their subtree.

A per-group *profile* was considered and rejected: profiles are isolated brains
(separate `HERMES_HOME`, config, gateway, memory), so one profile per class or
per team gives N brains with no shared org knowledge, N gateways and N Supabase
configs — the multi-tenant model **D1 explicitly rejects**. Groups partition
**visibility**, never the brain.

## Decisions applied

- **D1 — multi-user, not multi-tenant.** Groups are a visibility tier inside one
  brain.
- **D15 — per-item grants stay per-item.** Groups are the *set* case; grants are
  the *item* case. `item_grants` is **not** overloaded with group references (its
  single-active-assignee partial-unique index assumes a person).
- **Publishes C10 — group scope.** Extends **C2**; consumes **C1** (principal),
  **C3** (datastore routing), **C5** (change log), **C8** (trace).

## Reuse map

- `hermes_cli/access.py` — `scope_filter()`, `apply_scope_rls()`,
  `bind_principal()`, `_elevated_read_sql()`, `_role_rank_sql()`,
  `reads_by_elevation()`, `bind_elevated_reads()`, `_VALID_COLUMN`
  guards. **All extended in place.**
- FG-19 `item_grants` + `_grant_exists_sql` — the precedent for adding a clause
  to both the app filter and the RLS policy. Follow it exactly, including the
  table-qualified-`id_column` lesson recorded in FG-19's audit log.
- FG-21 `hermes.elevated_reads` GUC + `memory_access_audit` — the audit surface;
  gains a `via_group_id` column rather than a new ledger.
- `hermes_cli/datastore.py` (C3) — `SupabaseAppStore`; groups live in `app_*`.
- `hermes_cli/members.py` — `require_member_admin` authority pattern.

## Design / approach

### 1. Groups are a forest of typed dimensions

```sql
groups(
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  key          TEXT NOT NULL,                 -- human slug: 'eng.backend.payments'
  name         TEXT NOT NULL DEFAULT '',
  dimension    TEXT NOT NULL,                 -- 'org' | 'project' | 'cohort' | 'location' | …
  parent_id    UUID NULL REFERENCES groups(id) ON DELETE RESTRICT,
  elevation_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  created_by   TEXT NOT NULL REFERENCES principals(user_id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (dimension, key)
)
```

- **Multi-layered** = `parent_id`, arbitrary depth, one parent per group.
- **Multi-dimensional** = `dimension`; each dimension is an independent forest.
  A principal may be in as many groups across as many dimensions as needed.
- **Invariant (DB-enforced):** a child's `dimension` must equal its parent's, so
  axes never tangle. Enforced by trigger (a CHECK cannot see the parent row).
- **Invariant:** the parent graph is acyclic — enforced by the closure-table
  maintenance, which refuses a move that would make a group its own ancestor.
- `shared` is understood as **the implicit root of every dimension** — the
  audience of everyone. No row or table changes for it.

```sql
group_members(
  group_id   UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES principals(user_id) ON DELETE CASCADE,
  group_role TEXT NOT NULL CHECK (group_role IN ('admin','member')),
  added_by   TEXT NOT NULL REFERENCES principals(user_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (group_id, user_id)
)
```

```sql
group_closure(                       -- materialised transitive closure
  ancestor_id   UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  descendant_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  depth         INT  NOT NULL,
  PRIMARY KEY (ancestor_id, descendant_id)
)
```

Maintained on create / move / delete (rare operations). It exists so the read
path never runs a recursive CTE.

### 2. Two directions — the crux of the design

They are opposite, and both are required:

- **Read inheritance goes UP.** Membership in `payments` implies the audience
  `{payments, backend, eng}`; a row tagged `group:eng` is therefore readable by
  a payments engineer.
- **Admin scope goes DOWN.** Admin of `backend` covers `{backend, payments, …}`
  and anything added under it later.

Getting this backwards produces either "everyone reads the top team's rows" or
"a lead cannot see their own team".

### 3. Read predicate (extends C2)

A principal may read a row when **any** of:

1. they are instance `owner` (existing bypass); or
2. `visibility = 'shared'`; or
3. `visibility = private:<self>`; or
4. **(new, audience)** `visibility = ANY(my audience)` — my groups + their
   ancestors; or
5. **(new, admin scope)** `visibility = ANY(my admin scope)` — groups I admin +
   their descendants; **not** audited, because these are team-level rows, not a
   person's private data; or
6. **(changed, elevation)** the row is `private:<u>` where `u` is in my admin
   scope's membership, the covering group has `elevation_enabled`, and my
   `group_role` in that group outranks theirs — **always audited**; or
7. an active per-item grant exists (FG-19, unchanged).

Clause 5 vs. 6 is the deliberate split: **audit is for reading people, not for
reading teams.** If a lead's routine access to their team's runbooks generated
audit rows, the subject's ledger would bury the entries that matter.

### 4. Elevation must compare *group* roles, not instance roles

**This changes FG-21 P3 semantics and is the easiest thing to get wrong.**
`reads_role_below()` compares instance roles, and in a correctly configured
deployment every team lead is instance-role `member` (instance `admin` is
reserved for people who administer the deployment — an instance `admin`
outranks every member *globally*, which would make group scoping decorative).
Two `member`s rank equal, so the existing clause would refuse **every** group
elevation.

Therefore `_elevated_read_sql` gains a group-scoped variant that correlates
`group_members` instead of `principals`:

```sql
EXISTS (SELECT 1
        FROM group_members gm_subject
        JOIN group_closure gc ON gc.descendant_id = gm_subject.group_id
        JOIN groups g ON g.id = gc.ancestor_id
        JOIN group_members gm_reader
             ON gm_reader.group_id = gc.ancestor_id
            AND gm_reader.user_id = current_setting('hermes.principal_id', true)
        WHERE gm_subject.user_id = <owner_expr>
          AND gm_reader.group_role = 'admin'
          AND gm_subject.group_role <> 'admin'
          AND g.elevation_enabled)
```

Peer admins are blocked structurally: neither is an ancestor of the other, so no
row satisfies the join. The instance-wide `role_reads` switch is **retained but
deprecated** (single-user and IT-admin cases still use it); group elevation is
the path for organisational deployments.

### 5. Performance — expand once per request, not per row

A recursive CTE inside an RLS policy is evaluated per row. Instead,
`bind_principal()` binds two more transaction-local GUCs, each computed with one
query against `group_closure`:

| GUC | contents |
|---|---|
| `hermes.principal_groups` | audience: `group:<id>` tags for my groups + ancestors |
| `hermes.principal_admin_scope` | `group:<id>` tags for groups I admin + descendants |

The policy then does array membership:

```sql
OR visibility = ANY(string_to_array(
       current_setting('hermes.principal_groups', true), ','))
```

Two queries per request regardless of user count; constant-time per row.
**Fail-closed:** an unbound/empty GUC yields an empty array, so a connection
that forgets to bind reads exactly what plain C2 allows — the same property
`hermes.elevated_reads` already has. `bind_principal` must be **all-or-nothing**:
if group expansion fails, the group GUCs stay unset (degrade to plain C2) rather
than the request proceeding with a stale or partial set.

Short-circuit: a principal who administers a dimension **root** gets the whole
dimension, so the admin-scope GUC never needs to enumerate thousands of ids.

Index requirements: `group_members(user_id)`, `group_members(group_id)`,
`group_closure(descendant_id)`, and the existing `visibility` indexes serve the
array test.

### 6. Write side — who may tag what

To write `visibility = group:<g>` you must be a **member of `g`** or an **admin
of an ancestor of `g`**. You can only publish to an audience you belong to.
Enforced in the app layer *and* by a `WITH CHECK` clause on the RLS policy, so
the database refuses it too. Default write visibility stays `private:<self>`.

### 7. Group management authority + escalation containment

| operation | who |
|---|---|
| create a dimension root, move a group across parents/dimensions, delete a dimension | instance owner (+ instance admin) |
| create a sub-group **within a subtree I administer** | that subtree's group admin |
| add/remove members, set `group_role`, toggle `elevation_enabled` **within my subtree** | that subtree's group admin |
| grant myself admin of an ancestor / sibling | **nobody** — refused, audited |

Without the containment rule a lead creates a group under the dimension root,
makes themself admin, and has self-promoted. Every mutation emits C5
(`target_kind=data`, reversible `inverse_op`) and C8.

### 8. Membership changes take effect on the next read

Audience and admin scope are recomputed per request from the closure table and
never frozen into rows — so removing someone from a group revokes access
immediately, exactly as a role change already does. Group **deletion** is
refused while rows still reference `group:<id>` (`ON DELETE RESTRICT` on the
parent link plus an explicit reference check); `hermes group merge <from> <to>`
re-tags and then deletes, as one audited change.

### 9. Audit

`memory_access_audit` gains `via_group_id UUID NULL` (and the equivalent field
in the C8 trace payload), so the subject's ledger reads *"Ben read 3 of your
private memories, as admin of eng.backend"*. Both reader and subject can already
read the ledger; the `via_group_id` is what makes it an accountability record
rather than a bare log.

### 10. Explicitly out of scope for v1

- **Intersection scopes** (`backend ∧ apollo`). Union semantics only; if you
  need that audience, create the group. Intersection makes the predicate
  non-linear and much harder to audit.
- **Rule/attribute-driven dynamic membership** and **IdP (SCIM/OIDC) group
  sync.** Explicit membership first; sync is a later layer that writes the same
  tables. (This is the natural answer at 4-figure headcounts — see FG-26 §"open".)
- **Multiple groups per row.** One `visibility` tag; `item_grants` covers
  "…and also these three people". A `row_group_shares` side table is a clean
  later extension.
- **Group-of-groups by membership** (nesting is `parent_id` only).

## Data model summary

New in `app_*` (C3-routed): `groups`, `group_members`, `group_closure`.
Altered: `memory_access_audit + via_group_id`. Unchanged: `principals`,
`channel_identities`, `principal_aliases`, `item_grants`.

## Dev/Prod + Supabase

Tables created in `app_dev` first via C3, promoted to `app_prod` on the usual
owner-gated path. RLS `FORCE`d on all three new tables:
- `groups` — readable by any enrolled principal (a group's *existence* and name
  are not secret; its rows are protected by the scope policy);
- `group_members` — readable by the owner, by members of that group, and by
  admins of its ancestors. A member must not be able to enumerate the whole
  organisation's membership;
- `group_closure` — readable by any enrolled principal (structure only, no
  identities).

`agent_home_app` (NOBYPASSRLS) needs `SELECT` on all three.

## Testing requirements

- Unit: closure maintenance on create/move/delete; cycle refusal;
  cross-dimension parent refusal; audience and admin-scope expansion.
- **Negative access (required, real Postgres RLS — not app layer only):**
  - peer in the same group cannot read a peer's `private:` rows;
  - a sibling-team member cannot read `group:<other team>` rows even though they
    share an ancestor (the Model-B trap);
  - a group admin cannot read a **peer** group admin's private rows;
  - a group admin reads their subtree only — never a sibling subtree;
  - cross-dimension isolation: admin of `apollo` gets nothing in `org`;
  - **unbound GUC** ⇒ plain C2 (fail-closed);
  - elevation refused when `elevation_enabled = false`, allowed when true, and
    **audited with the correct `via_group_id`** when it fires.
- Authority/escalation: a subtree admin cannot create a sibling of their root,
  cannot re-parent their group, cannot self-admin an ancestor; refusals audited.
- Write side: tagging a row `group:<g>` refused (app **and** `WITH CHECK`) for a
  non-member/non-ancestor-admin.
- Revocation: removing a member revokes on the next read within the same
  process, no cache.
- Lifecycle: group delete refused while referenced; `merge` re-tags atomically.
- **Performance (required):** with ≥500 principals, ≥100 groups and ≥50k scoped
  rows, a scoped read stays within the pre-change plan shape — assert
  `EXPLAIN` shows no per-row recursive CTE and the GUC expansion is 2 queries.
- Baseline: a deployment with **no groups** produces byte-identical predicates
  to pre-change (regression-locked).

## System testing (system-test box)

Required before promotion, on `hermes-systest` against `app_dev`, with ≥4 real
principals arranged as `eng → backend → payments` + `frontend`:
the full read matrix from the FG doc verified through the live API (not psql),
one elevated read performed and then found in the subject's own ledger with the
right `via_group_id`, and one escalation attempt refused and audited.

## Dependencies

- **Blocked by:** FG-01 (C1/C2), FG-13 (C3), FG-12 (C5), FG-16 (C8). All merged.
- **Blocks:** FG-26 (the Groups UI renders this).
- **Related:** FG-19 (`item_grants` — coexists), FG-21 P3 (elevation — semantics
  changed here), FG-24 (independent).

## Definition of Done

C10 published (typed helpers + docstrings); groups/closure/membership with
DB-enforced dimension + acyclicity invariants; audience + admin-scope clauses in
**both** `scope_filter` and `apply_scope_rls`; GUC expansion in `bind_principal`
with fail-closed degradation; group-role-based elevation replacing instance-role
comparison for group reads, audited with `via_group_id`; escalation containment
enforced + audited; full negative-access matrix green on **real Postgres**;
performance assertion green; no-groups baseline byte-identical;
`scripts/run_tests.sh`, `ruff`, `ty` clean; system test passed.

## Progress checklist

- [ ] `groups` / `group_members` / `group_closure` + dimension & acyclicity triggers + closure maintenance
- [ ] `visibility` grammar extended to `group:<id>`; `normalize_visibility` / parsers
- [ ] `scope_filter` audience + admin-scope clauses (params, `start_index` offsets)
- [ ] `apply_scope_rls` mirrors + `WITH CHECK` write-side clause
- [ ] `bind_principal` group GUC expansion (all-or-nothing, root short-circuit)
- [ ] Group-scoped elevation (group roles, not instance roles) + `elevation_enabled` per group
- [ ] `memory_access_audit.via_group_id` + C8 payload field
- [ ] Group CRUD service + CLI (`hermes group create|move|add|remove|set|merge|list`) with containment authority + C5/C8
- [ ] RLS on the three new tables + `agent_home_app` grants
- [ ] Tests: unit, negative-access matrix (real Postgres), authority/escalation, write-side, revocation, lifecycle, performance, no-groups baseline
- [ ] System test on `hermes-systest` passed

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo: the design must be general-purpose (school *and* medium business) and support multi-layered / multi-dimensional organisational topologies. Chose a typed forest (`dimension` + `parent_id`) with a materialised closure and two pre-expanded GUCs over per-row recursive CTEs; separated read inheritance (up) from admin scope (down); split audit so it fires for reading *people* not *teams*; and identified that group elevation must compare **group** roles because every team lead is instance-role `member` (instance-role comparison would refuse every elevation). |

## Cloud-agent prompt

> **[Phase-6 Wave A — may run in parallel with FG-24; blocks FG-26]** Repo
> `leolau/ai-prentice-4-all`, branch off `develop`. Read
> `docs/design/master-plan/README.md`, `AGENTS.md`, and this doc (FG-25). Add a
> **group tier to C2** — `visibility ∈ {shared, group:<group_id>,
> private:<user_id>}` — as an **extension of the existing predicate, not a new
> access system**: follow exactly how FG-19's grant clause and FG-21's elevation
> clause were added to `scope_filter()` **and** `apply_scope_rls()` in
> `hermes_cli/access.py`. Groups form a **forest of typed dimensions**
> (`dimension` + `parent_id`, DB-enforced same-dimension parent + acyclicity)
> with a materialised `group_closure`. **Read inheritance goes UP** (audience =
> my groups + ancestors), **admin scope goes DOWN** (groups I admin +
> descendants) — do not conflate them. Expand both **once per transaction** in
> `bind_principal()` into `hermes.principal_groups` /
> `hermes.principal_admin_scope` GUCs and test them with array membership in the
> policy; **never** put a recursive CTE in an RLS policy. Unbound GUC ⇒ plain C2
> (fail-closed), and expansion failure must leave the GUCs unset rather than
> proceed. Reading a **team** row via admin scope is **not** audited; reading a
> **person's** `private:` row is elevation — per-group `elevation_enabled`, off
> by default, **always** audited with a new `memory_access_audit.via_group_id`.
> **Critical:** group elevation must compare **`group_members.group_role`**, not
> instance roles — every team lead is instance-role `member`, so the existing
> `reads_role_below` comparison would refuse every elevation. Enforce write-side
> tagging (member of the group or admin of an ancestor) in the app layer **and**
> a `WITH CHECK` clause. Enforce **escalation containment**: a subtree admin may
> only create/administer within their own subtree; roots and cross-parent moves
> are owner-only; refusals audited. Do **not** overload `item_grants` with
> groups, do **not** add intersection scopes, dynamic membership or multiple
> tags per row. Tests must include the full **negative-access matrix on real
> Postgres RLS** (peer/peer, sibling-team, admin-vs-peer-admin, subtree-only,
> cross-dimension, unbound GUC, elevation off/on + audit contents), escalation
> refusals, revocation-on-next-read, lifecycle (delete refused while referenced,
> `merge`), a **performance assertion** at ≥500 principals / ≥100 groups / ≥50k
> rows, and a **no-groups baseline** proving byte-identical predicates. Run
> `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking
> this doc.
