# FG-26 — Users admin console + invitation activation

**Wave:** P6-B (Phase-6, after FG-27) · **Owner agent:** _unassigned_ · **Status:** PLAN — not started, rescoped 2026-08-12

> **Rescoped 2026-08-12 — groups are out, and the profile picker is settled.**
> FG-25 was deferred (profiles, not groups, carry the cohort structure), so
> everything in this FG that managed groups has been removed rather than left
> for the next agent to discover: group CRUD, group-admin assignment, the
> `elevation_enabled` toggle, and the `/me/access` elevation ledger. Sections
> that were group-scoped are now profile-scoped, which is the isolation this
> deployment actually has. See "Removed with FG-25" for the list and where each
> item went, so nothing is silently dropped.

## Summary

`agent-home` today has a single-purpose `/members` page: a flat, unpaginated,
unsearchable list plus a create form that **generates a temporary password in
the browser** for the owner to relay by hand. That is workable for one owner and
a handful of members and unusable at 500.

This FG replaces it with a proper administration surface:

1. a **Users** page — a directory every enrolled principal can see, with
   management actions gated to owner/instance-admin;
2. **User** CRUD, with a **single-use, short-lived invitation link** replacing
   admin-set passwords: the invitee sets their own password on activation;
3. the supporting surfaces the above imply — self-service password reset,
   channel linking, and a bulk import path.

## Decisions applied

- **D16 (agent-home BFF).** All new endpoints follow the existing pattern: the
  browser talks to `agent-home` route handlers; those hold the C1 principal and
  proxy authority operations to the Python `/api/*`. **The browser never sees
  the service-role key**, and the BFF's authorization is UX only — the Python
  layer re-checks everything.
- **D1/C2.** The UI renders access; it never decides it.
- New requirement locked this session (Leo): **invitation link, 5-minute
  validity, shared out-of-band by the owner/admin; the new user sets their own
  password.**
- **"Assign profile" — resolved 2026-08-12 (Leo).** *Owner/admin selects which
  profile a new user belongs to.* See below for what ships in this FG and what
  waits for FG-28.

## Resolved — "assign profile" (was blocking §3.1)

Leo's rule: **the owner/admin chooses the profile at creation time.** Not
self-selection, and not an implicit "whichever console you happen to be in".

The original three readings in this doc are obsolete: they predate the
2026-08-10 shared-Supabase decision. Because **all profiles share one Supabase**,
they share one GoTrue and one `auth.users`, so an *account* is already box-wide
and no shared identity store is needed. "Which profile does Dana belong to" is
simply **which profile's `principals` table gets her row** — and, since a person
may participate in several profiles (FG-29's model), she may hold a row in more
than one.

What blocks the picker is not identity, it is **FG-27**: a process running as
profile A cannot open profile B's schema — `SupabaseAppStore.connect()`
fail-closes on the ownership claim, deliberately, because that guard is what
keeps two profiles' rows from interleaving. So enrolling into another profile
from this console requires a process entitled to *both* schemas, which is
exactly the control plane **FG-28** builds.

**Decided (Leo, 2026-08-12) — option 1 of two:**

| | | |
|---|---|---|
| **chosen** | FG-26 ships the picker **scoped to the profile being administered** (the field is present, explicit and audited — one option today, and the create flow is already built around a chosen target rather than an implicit one). Cross-profile assignment arrives with **FG-28**. | no new privileged path |
| rejected | An owner-only cross-profile enrolment route inside FG-26. | a second privileged door, weeks before FG-28 builds the first one properly — and it would have to be unbuilt again |

Consequences for the implementer:

- The create form's profile field is **required** and its value travels to the
  server; the server refuses a target other than the profile it is running as
  (`409`, naming FG-28), rather than ignoring the field. A field that is
  silently ignored is how the wrong profile gets a user later.
- The users list and the directory are **profile-scoped** and say so on screen —
  "the people enrolled in *this* profile", not "everyone on this box". With one
  shared `auth.users`, "all accounts" and "the people in this brain" are
  genuinely different sets, and conflating them in the UI is a data-exposure
  bug, not a copy nit.
- **FG-28 inherits** the cross-profile picker, and the API shape here must not
  make it awkward: `POST /api/comms/members` takes `profile` from day one.

## Reuse map

- **Backend:** `hermes_cli/members.py` (`MemberService`, `GoTrueAdminClient`,
  `require_member_admin`, `MemberView`), `hermes_cli/web_server.py`
  `/api/comms/members*` + `_comms_resolve_principal` (`allow_as=False` on
  writes), `hermes_cli/access.py` C1/C2, `hermes_cli/changes.py`
  (C5), `interactions.py` (C8), and `hermes_cli/datastore_binding.py` +
  `hermes datastore show` (FG-27) for the profile label the console displays.
- **Frontend:** `agent-home/src/components/members/MembersView.tsx` (evolves
  into the Users page), `lib/auth/principal.ts` (`requirePrincipal`,
  `apiClientForRequest`), `components/nav-items.ts`, `components/ui/*`,
  `MobileShell`. Every new component root element carries
  `data-component="<ComponentName>"` (repo convention, 72 files already do).

## Design / approach

### 1. Users page (`/users`, replaces `/members`)

Two audiences, one page, server-rendered with the principal already resolved:

- **Everyone (any enrolled principal):** a read-only **directory** — display
  name and whether the person is active. Not email, not channel ids, not
  roles-as-management. Rationale: a member of a 500-person org needs to know who
  else exists (to assign a task, to share an item), but the directory must not
  become an address book export. Its scope is **this profile's principals** —
  never `auth.users`, which is box-wide and includes people enrolled only in
  other profiles.
- **Owner / instance-admin:** the same list plus management columns (email,
  role, account state, channels) and row actions.

Required at this scale, and absent today:
- **server-side pagination** (page size 50) — `/api/comms/members` gains
  `?limit&offset&q&role&active`;
- **search** on display/email and **filter** by role / status;
- **fix the N+1**: `PrincipalStore.list_principals()` currently issues one
  `_channels_for()` query **per principal** (501 queries for 500 users). Replace
  with a single grouped query. This is a prerequisite for the page, not a
  nice-to-have.

### 2. Removed with FG-25 (do not build these here)

FG-25's group tier is deferred, so these are **not** part of this FG. Listed
explicitly because they were in the plan and their absence would otherwise read
as an oversight:

| removed | why / where it went |
|---|---|
| `/groups` page, group CRUD, membership editor, merge-into-parent | there is no `groups`/`group_members` table; profiles carry the cohort structure (FG-25 deferral note) |
| group-admin (`group_role`) assignment and subtree containment | no scoped-admin tier exists; instance roles only |
| `elevation_enabled` toggle | the elevation it gates is FG-25's; instance-wide `role_reads` (FG-21 P3) remains as-is |
| `/me/access` "who read my data" ledger | it is the visible half of FG-25 elevation and must ship **with** it, not before — a ledger of a mechanism that does not exist is worse than no page |
| group filters in the directory and members list | replaced by profile scope, which is isolation by construction rather than by policy |

If a single large profile later needs internal audiences, FG-25 is revived as
written and this console gains the pages then.

### 3. User CRUD + invitation activation

#### 3.1 Create (owner / instance-admin)

Form: **profile** (required; see "Resolved" above — the profile being
administered, and the server refuses any other), **login name (email)**, display
name, and instance role (`admin`/`member`/`viewer` — never `owner`). On submit
the server, in order:

1. validates the requested `profile` against the profile it is running as, and
   refuses with `409` naming FG-28 if they differ — before creating anything;
2. creates the GoTrue account **with a random unguessable password and
   `banned = true`** — so a created-but-unactivated account cannot be logged
   into, and the temporary-password relay disappears entirely;
3. enrols the principal (existing `create_member` flow, including the GoTrue
   rollback-on-enrolment-failure guard — an account that can log in but has no
   principal would authenticate and then hit the 409);
4. mints an invitation and returns the link **once**.

**The account may already exist**, because accounts are box-wide: someone
enrolled in another profile who is now being added to this one needs a
`principals` row, not a second account. The form must handle "this email already
has an account" as enrolment (and skip the invitation — they already have a
password), not as an error, or the shared-GoTrue topology turns the common
second-profile case into a dead end.

#### 3.2 Invitation model

```sql
invitations(
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      TEXT NOT NULL REFERENCES principals(user_id) ON DELETE CASCADE,
  token_hash   BYTEA NOT NULL,          -- SHA-256 of the token; the token itself is never stored
  expires_at   TIMESTAMPTZ NOT NULL,
  used_at      TIMESTAMPTZ NULL,
  revoked_at   TIMESTAMPTZ NULL,
  created_by   TEXT NOT NULL REFERENCES principals(user_id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

- Token: **32 bytes from `secrets.token_urlsafe`**, shown to the admin exactly
  once, **stored only as a hash** — a database leak cannot activate accounts.
- **Validity: 5 minutes** (`invitations.ttl_seconds` in `config.yaml`, default
  300). Recorded caveat: 5 minutes assumes the admin is with the invitee or in a
  live chat with them. Because it will expire during any asynchronous handover,
  a prominent **"Regenerate link"** action on the user row is part of the
  feature, not an afterthought.
- **Single-use** (`used_at`), **revocable** (`revoked_at`), invalidated by a
  newer invitation for the same user.
- Verification is **constant-time** on the hash, and the redeem endpoint is
  **rate-limited per IP and per token** — an unauthenticated endpoint that grants
  account control is the highest-value target this feature adds.
- Link form: `https://<agent-home>/activate/<token>` — token in the **path**, no
  PII, `noindex`, `Referrer-Policy: no-referrer` on that route so the token
  cannot leak through a referrer header.

#### 3.3 Activation flow (unauthenticated, token-gated)

```
GET  /activate/<token>            → validate (unexpired, unused, unrevoked) → show
                                     the account's email + a set-password form,
                                     or a neutral "this link is no longer valid"
POST /api/auth/invitations/redeem → { token, password }
     server: re-validate → GoTrue admin set_password → unban the account
             → mark used_at → C5 + C8 audit → redirect to login
```

Notes:
- The server-side handler is the **only** holder of the service-role key.
- Password policy enforced server-side (length ≥ 12, not the email, etc.).
- Failure responses are deliberately uniform ("no longer valid") so the endpoint
  is not an oracle for which tokens/accounts exist.
- Implemented as **our own `invitations` table**, not GoTrue's
  `admin/generate_link`: GoTrue's link expiry is a single global setting
  (`GOTRUE_MAILER_OTP_EXP`), it is oriented around sending email (which this
  deployment does not do), and we need single-use + revoke + regenerate + audit.

#### 3.4 Update / delete

- **Update:** display, instance role (never `owner`; owner transfer stays the
  approval-gated CLI path), activate/deactivate.
- **Delete:** default action is **deactivate** (GoTrue ban) — login stops
  immediately while `owner_user_id` attribution and audit history stay intact
  and correctly attributed. Hard delete is a separate, confirmation-gated
  action that must first answer *what happens to their data* — the dialog
  requires choosing **transfer ownership of their rows to <principal>** or
  **delete their private rows**, because `ON DELETE CASCADE` on
  `channel_identities` does **not** touch their memories, files
  or GTS items, which would otherwise be orphaned under a dangling
  `owner_user_id`.
- **Self-protection:** an admin cannot demote or deactivate themselves out of
  the last admin seat, and cannot delete themselves. Refused server-side.

#### 3.5 Enrolment-level vs account-level operations (shared-Supabase constraint)

**Decided 2026-08-10: all profiles share one Supabase instance**, so they share
one GoTrue and one `auth.users`. That makes an *account* a box-wide object while
*authority* stays per profile — and the operations in §3.4 are not all the same
kind of thing:

| kind | operations | blast radius |
|---|---|---|
| **enrolment-level** | add / remove / re-role the `principals` row; suspend / restore the enrolment | the acting profile only |
| **account-level** | delete, set/reset password, GoTrue ban | **every profile the account is enrolled in** |

As built, "deactivate" moved from the second row to the first: it flips `active`
on this profile's `principals` row instead of banning the shared `auth.users`
row, because a ban would lock the person out of profiles this console has no
authority over. A member's box-wide ban state (never activated, or banned) and
their profile-local enrolment state are therefore two separate fields on
`MemberView`, and the row distinguishes "awaiting activation" from "suspended".

A profile-local flag is only a control where the profile grants authority, and
the review found the flag was written but not read: a suspended person kept
their role on every messaging channel, kept every `/api/comms/*` surface, and
stayed a candidate for the FG-24 memory binding. So `active` is now enforced at
the three seams that grant authority — `resolve_principal` (C1) answers *nobody*
for a suspended identity and pairing cannot re-admit them, the web resolver
answers **403** for reads as well as writes, and the FG-24 ladder drops a
suspended binding. The account itself is untouched, which is the point: it signs
in, and this profile refuses it by name ("suspended in this profile") rather than
by pretending the password is wrong.

`MemberService` currently performs the account-level ones through the GoTrue
**admin** API with the service-role key, gated only by `require_member_admin`,
which checks the actor's role *in the current profile*. So an admin of `hr`
banning a user also enrolled in `engineers` revokes their access there too — a
per-profile authority exercised through a globally-scoped credential.

Requirements:

- **Deactivate, in a profile context, means un-enrol** (remove/disable the
  `principals` row), **not** ban the account. Login continues to work; the user
  simply has no authority in that profile. This is the correct per-profile verb
  and it is the default the UI offers.
- **Account-level operations require either owner, or that the target is
  enrolled solely in profiles the actor administers** — checked server-side
  across profiles, not assumed. The dialog must say plainly which profiles are
  affected before confirming.
  **In FG-26 this check cannot be performed**, and that is the same FG-27 wall
  as the profile picker: enumerating the target's other enrolments means reading
  other profiles' `principals` tables, which this process is refused. So in
  FG-26 account-level operations are **owner-only**, and the admin path to them
  arrives with FG-28's control plane. Restricting rather than approximating is
  deliberate: a check that cannot see the other profiles would confidently
  report "affects this profile only" while banning someone out of three.
- **Password reset is account-level** and therefore subject to the same rule;
  it cannot be a routine per-profile-admin action once accounts are shared.
- **Every per-profile process holding the shared service-role key is the same
  problem from the other side** — that key can mint an account valid in every
  profile, so compromising one profile's process is a box-wide account-system
  compromise. Preferred direction: account-level operations move behind a single
  control-plane service (see FG-28) and stop being reachable from each profile's
  process. Recorded here as the open decision it is.

### 4. What else is needed (answering "anything else?")

Ordered by how soon each is required:

1. **Self-service password reset.** Today only an admin can set a password. Reuse
   the same token mechanism with `kind='recovery'` and a longer TTL. Without it,
   every forgotten password at 500 users is an admin ticket.
2. **Bulk import.** CSV → (email, display, role) with a dry-run preview,
   per-row validation, and a downloadable file of the generated links. Creating
   500 users one form at a time is not a workflow.
3. **Channel linking UI.** `channel_identities` is CLI-only today (and empty in
   production), so every inbound message lands on the owner fallback. A "link
   Telegram/WhatsApp" action — admin-initiated or a self-service pairing code —
   is what actually turns multi-user channels on. With FG-24 landed, this is
   also what stops channel-less sessions falling back to a remembered local
   binding.
4. **Admin activity view.** Filter the existing C5 change log to identity events
   (created / role changed / invited / activated / deactivated / deleted) so
   administration is reviewable. Reuses `/api/comms/changes`.
5. **Empty / error / permission states.** A member hitting a management route
   gets a clean "you don't have access", never a raw 403 or a blank page.
6. **Seat + cost visibility** (nice-to-have): principal count and per-principal
   interaction/cost from C8, so growth is observable before it is a bill.

Deliberately **not** in this FG: owner transfer (stays approval-gated CLI),
SSO/SCIM provisioning (the right answer at 4-figure headcount; a later FG that
writes the same tables), and per-principal rate/cost quotas (runtime concern,
separate FG).

## Data model

New: `invitations` (above), in the **administered profile's schema** (FG-27), so
an invitation is scoped to the brain it enrols into even though the account it
activates is box-wide. Everything else reuses `principals`,
`channel_identities`, `changes` (C5),
`interactions` (C8). RLS `FORCE`d on `invitations`: readable only by the owner
role and `created_by`; **never** exposed through any authenticated list endpoint
in a form containing the token (only `expires_at` / `used_at` / `revoked_at`).

## API surface (additive)

```
GET    /api/comms/members            + ?limit&offset&q&role&active         (paginated)
GET    /api/comms/directory                                    (any principal, this profile)
POST   /api/comms/members            + profile  → returns { member, invitation_url }
                                                  409 if profile != the running one (FG-28)
POST   /api/comms/members/{id}/invitation                      (regenerate)
DELETE /api/comms/members/{id}/invitation                      (revoke)
DELETE /api/comms/members/{id}       ?strategy=transfer|purge  (hard delete)
POST   /api/auth/invitations/redeem                            (UNAUTHENTICATED, rate-limited)
```

All write paths resolve the principal with `allow_as=False` (an `?as=` header
must never let an admin act *as* someone else), and all emit C5 + C8.

## Testing requirements

- **Invitation security (required):** expired / used / revoked / unknown /
  tampered tokens all refused with an identical response; token never appears in
  any list response, log line, or C5 payload; only the hash is stored;
  redemption is rate-limited; a second redemption of the same token fails.
- Created-but-unactivated accounts **cannot log in** (banned until redeem).
- Redeem sets the password, unbans, marks `used_at`, and audits — verified
  against real GoTrue, not a mock.
- Authority: member/viewer receive 403 on every management endpoint; nobody can
  create/assign `owner`; self-demotion/self-deletion refused; last-admin
  protection.
- **Profile scope (new, and the FG-27 boundary):** `POST` with a `profile` other
  than the running one is refused with 409 and **creates no GoTrue account** (an
  orphan account is the failure mode worth a test of its own); the directory and
  members list never return a principal enrolled only in another profile; a
  second profile's console shows a disjoint roster on the same database —
  asserted on real Postgres with two derived schemas, reusing FG-27's fixture.
- **An existing account being enrolled into a second profile** succeeds as
  enrolment, mints no invitation, and does not touch the account's password.
- Pagination/search/filter correctness, and a **query-count assertion** proving
  the N+1 is gone (one query for channels, not one per principal).
- Hard delete: both strategies leave no row with a dangling `owner_user_id`.
- Frontend: vitest for the Users view (states, permission gating,
  optimistic-update rollback on 403), `data-component` present on new roots,
  lint/typecheck/build green.
- E2E: admin creates a user → link generated → **link expires** → regenerate →
  invitee activates and sets a password → logs in → sees this profile's rows and
  nothing from another profile on the same database.

## System testing (system-test box)

On `hermes-systest` against `app_dev`, from a real phone/browser: create a user;
confirm the 5-minute expiry against the wall clock; regenerate; activate from a
second device; confirm the new user sees this profile's rows and none of the
`maintenance` profile's; confirm deactivate stops their authority here
immediately; and confirm the console names the profile it is administering (the
field exists precisely so nobody has to infer it).

## Dependencies

- **Blocked by:** **FG-27** (merged — profile-derived schemas and the ownership
  guard are what make "this profile's users" a real set), FG-20 (agent-home
  BFF), FG-01 (C1/C2), FG-12 (C5), FG-16 (C8). **No longer blocked by FG-25**,
  which is deferred.
- **Related:** FG-24 (per-principal memory — channel linking here is what stops
  the local-binding fallback), FG-28 (inherits cross-profile assignment and the
  account-level control plane), FG-17 (operator dashboard — not duplicated here;
  this is the `agent-home` surface).
- **Blocks:** any real multi-user rollout.

## Definition of Done

Users page shipped with pagination/search/filter and permission-gated actions;
the create form carries a **required profile** whose mismatch is refused with
409 and no orphan account; invitation create/regenerate/revoke/redeem with
hashed single-use tokens, 5-minute default TTL, rate limiting and full audit;
accounts banned until activation; an existing box-wide account enrols into this
profile without a new invitation; self-service reset; bulk CSV import; channel
linking; N+1 removed with a query-count test; profile scope asserted on real
Postgres with two schemas; all backend + frontend tests green;
`scripts/run_tests.sh`, `ruff`, `ty`, `npm run lint`, `tsc`, `next build`
clean; system test passed.

## Progress checklist

- [x] Resolve the "assign profile" open decision — owner/admin selects the profile; scoped to the administered profile, cross-profile deferred to FG-28 (Leo, 2026-08-12)
- [x] `list_principals` N+1 fix + paginated/searchable `/api/comms/members`
- [x] `/users` page: directory (all) + management (owner/admin), pagination, search, filters, and the administered profile named on screen
- [x] `invitations` table + mint/regenerate/revoke + hashed single-use tokens + rate limiting + RLS
- [x] Create-user flow: required `profile` (409 + no orphan account on mismatch) → banned GoTrue account → principal → invitation link (shown once); an existing account enrols without one
- [x] `/activate/<token>` page + unauthenticated `redeem` endpoint + password policy + audit
- [x] Update / deactivate (profile-local suspend) / hard-delete (transfer|purge) + self-protection + last-admin guard
- [x] Self-service password reset (recovery token)
- [x] Bulk CSV import with dry-run + generated-links export
- [x] Channel-linking UI
- [x] Admin activity view over C5
- [x] Tests (invitation security, authority, profile scope on two real schemas, pagination/query-count, delete strategies, frontend, E2E)
- [ ] System test on `hermes-systest` passed

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-12 | 3 | devin (for Leo) | Built FG-26: users console, invitations, activation, profile-scoped enrolment | Everything except the live-box system test is implemented; what follows is the decisions that were not in the doc. **Deactivate is now enrolment-local, not a GoTrue ban.** The doc's §"kind / operations / blast radius" table put "deactivate" under *account-level*, but a ban on a shared `auth.users` row locks the person out of every profile — including profiles this console has no authority over — so the reversible suspend became an `active` flag on the profile's own `principals` row, and `MemberView` reports the box-wide ban state and the profile-local enrolment state as two separate fields. Consequence worth knowing: a member can be *enrolled* here and still unable to log in because they never activated, which the row labels "awaiting activation" rather than "suspended". Account-level delete stayed owner-only, per the settled gate. **`invitations` carries a `kind`** (`activation` | `recovery`) so self-service reset reuses one hashed-single-use-token mechanism instead of a second one, with its own longer TTL (3600s) in `config.yaml` beside the 300s activation TTL. Minting revokes older open tokens of the same kind for that user, which is what makes Regenerate actually invalidate the link already in somebody's inbox. **Ownership resolution is discovered, not hard-coded**: `hermes_cli/ownership.py` reads `information_schema` for `owner_user_id` columns in the profile schema, so a table added later cannot silently start orphaning rows on delete; audit tables are excluded because rewriting history is worse than a dangling id, and a post-condition query asserts no dangling owners remain. **A refusal must not leave an orphan**, so the profile check runs before the GoTrue call, and a failed `principals` insert deletes the account it just created — both have their own tests. Frontend: `/members` now redirects to `/users` (old links survive), `generatePassword` and the whole temporary-password relay are deleted, and `/activate/<token>` is `noindex` with `Referrer-Policy: no-referrer` because until it is redeemed the URL *is* the credential. Not done: the live `hermes-systest` run, left for the parent session, and cross-profile assignment (FG-28). |
| 2026-08-12 | 2 | devin (for Leo) | Rescoped off the deferred FG-25; "assign profile" resolved | Two things made this doc unbuildable as written, and both are now closed. **Groups:** FG-25 was deferred on 2026-08-10, but this FG still had a `/groups` page, group-admin assignment, `elevation_enabled` and the `/me/access` ledger in its checklist — 4 of 15 items against tables that will not exist. Whoever picked it up would have built the deferred model or guessed, so they are removed *and listed* in a "Removed with FG-25" table with where each went; group filters become **profile** scope, which is isolation by construction rather than by policy. **Assign profile:** Leo's rule is that the owner/admin selects the profile. The doc's original A/B/C readings were obsolete — they predate the shared-Supabase decision, under which an account is already box-wide and "which profile" is just which `principals` table gets the row, needing no shared identity store. The real constraint is FG-27's ownership guard: a process running as profile A cannot open B's schema, by design. Leo chose to ship the picker scoped to the administered profile and hand cross-profile assignment to FG-28 (which builds the control plane entitled to several schemas) rather than add a second privileged door now. Two consequences are written in because they are silent-failure shaped: the `profile` field must be **refused with 409, not ignored**, when it names another profile (and must create no orphan GoTrue account on the way out), and "all accounts" ≠ "the people in this brain" — with one shared `auth.users`, listing the former in a profile console is a data-exposure bug, not a copy nit. Also recorded: an existing account being added to a second profile is an **enrolment**, not an error and not a new invitation — the common case under this topology, and a dead end if the form treats a duplicate email as a failure. |
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo's UI requirements: a Users page scoped by access, owner/admin CRUD over groups and users, and creation that assigns groups + login name and issues a **5-minute invitation link** so the new user sets their own password. Replaces the current browser-generated temporary-password relay in `MembersView.tsx`. Chose an own `invitations` table over GoTrue `admin/generate_link` (global-only expiry, email-oriented, no single-use/revoke/audit); accounts are created **banned** so an unactivated account cannot be logged into; hard delete must resolve data ownership because cascades do not reach memories/files/GTS. Flagged **"assign profile" as an open decision** — a profile is an isolated brain, not a user attribute, so the requirement as stated cannot be implemented without cross-profile identity. |

## Cloud-agent prompt

> **[Phase-6 Wave B — FG-27 has merged; FG-25 is deferred and NOT a dependency]**
> Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read
> `docs/design/master-plan/README.md`, `AGENTS.md`, FG-27, and this doc (FG-26)
> — including "Removed with FG-25", which lists what deliberately is **not** in
> scope. Build the **Users admin console** on `agent-home` plus **invitation
> activation**. Follow the FG-20 BFF pattern — route handlers hold the C1
> principal and proxy to the Python `/api/*`; the browser never sees the
> service-role key; BFF checks are UX only and Python re-checks everything.
> Replace `/members` with `/users`: a C2-scoped **directory** for every
> principal plus management columns/actions for owner/instance-admin, with
> **server-side pagination, search and filters** — and first fix the
> `PrincipalStore.list_principals()` **N+1** (one `_channels_for()` query per
> principal) with a single grouped query, asserted by a query-count test. Scope
> both the list and the directory to **this profile's principals**, never
> `auth.users` (accounts are box-wide; the two sets differ), and name the
> administered profile on screen. **User creation:** the form's **`profile` field
> is required**; the server refuses a value other than the profile it runs as
> with `409` naming FG-28, **before** creating anything — do not ignore the field
> and do not leave an orphan GoTrue account behind. An email that already has an
> account is an **enrolment** into this profile (no new invitation, password
> untouched), not an error. Then create the GoTrue account **banned, with a
> random password** (never
> relay an admin-chosen password — delete the browser-side `generatePassword`
> path), enrol the principal (keep the existing rollback-on-enrolment-failure
> guard), then mint an **invitation**: 32-byte
> `secrets.token_urlsafe`, **stored only as a SHA-256 hash**, single-use,
> revocable, **5-minute default TTL** (`config.yaml`, not an env var), shown to
> the admin exactly once, with a **Regenerate** action. Add
> `/activate/<token>` + an **unauthenticated, rate-limited**
> `POST /api/auth/invitations/redeem` that re-validates in constant time, sets
> the password via the admin API, **unbans**, marks used, and audits (C5+C8);
> every failure returns an identical neutral response. Deactivate is the default
> "delete"; hard delete must require a `transfer|purge` strategy for the user's
> rows (cascades do **not** reach memories/files/GTS). Add self-service password
> reset, CSV bulk import with dry-run, channel
> linking, and an identity-events view over C5. Enforce self-protection
> (no self-demotion/self-deletion, last-admin guard) and `allow_as=False` on
> every write. New component roots carry `data-component="<ComponentName>"`.
> Tests per this doc, including invitation-security negatives against **real
> GoTrue** and profile scope on **two real derived schemas** (reuse FG-27's
> Postgres fixture in `tests/hermes_cli/test_fg27_layer2_e2e.py`). Run
> `scripts/run_tests.sh`, `ruff`, `ty`, and the agent-home lint/typecheck/build.
> The "assign profile" decision is **resolved** — build it as specified, and do
> not reopen it. Open a PR linking this doc.
