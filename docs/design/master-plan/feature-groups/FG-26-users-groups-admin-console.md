# FG-26 — Users & Groups admin console + invitation activation

**Wave:** P6-B (Phase-6, after FG-25) · **Owner agent:** _unassigned_ · **Status:** PLAN — not started

## Summary

`agent-home` today has a single-purpose `/members` page: a flat, unpaginated,
unsearchable list plus a create form that **generates a temporary password in
the browser** for the owner to relay by hand. That is workable for one owner and
a handful of members and unusable at 500.

This FG replaces it with a proper administration surface:

1. a **Users** page — a directory every enrolled principal can see, with
   management actions gated to owner/instance-admin;
2. **Groups** CRUD (create / read / update / delete) over the FG-25 tier,
   including membership and group-admin assignment;
3. **User** CRUD, with a **single-use, short-lived invitation link** replacing
   admin-set passwords: the invitee sets their own password on activation;
4. the supporting surfaces the above imply — self-service password reset,
   channel linking, the "who read my data" ledger view, and a bulk import path.

## Decisions applied

- **D16 (agent-home BFF).** All new endpoints follow the existing pattern: the
  browser talks to `agent-home` route handlers; those hold the C1 principal and
  proxy authority operations to the Python `/api/*`. **The browser never sees
  the service-role key**, and the BFF's authorization is UX only — the Python
  layer re-checks everything.
- **D1/C2 + C10 (FG-25).** The UI renders access; it never decides it.
- New requirement locked this session (Leo): **invitation link, 5-minute
  validity, shared out-of-band by the owner/admin; the new user sets their own
  password.**

## ⚠️ Open decision — "assign profile" (blocks §3.4)

The requirement as stated is *"upon create new user, owner/admin needs to assign
**profile**, groups and login name."* A **profile is an isolated Hermes instance**
(its own `HERMES_HOME`, config, `.env`, gateway process and — critically — its
own database configuration). Principals are rows inside **one** profile's
database. There is therefore no table in which "Dana's profile" could be
recorded, and a profile picker cannot be implemented as stated without making
one identity span multiple brains.

Three readings, for Leo to pick:

| # | Reading | Cost |
|---|---|---|
| **A** | *"Profile" meant the user's **display profile*** (name, avatar, title). | Trivial; already partly there (`display`). **Assumed by this doc until told otherwise.** |
| **B** | Users belong to the profile whose console you are logged into; no picker. | Zero. Just a label showing which profile you are administering. |
| **C** | **Cross-profile identity** — one login spans several brains. | Large: a shared identity store above the per-profile databases, cross-profile session routing, and a new answer to "which brain's memory does this user's data live in". A separate FG, not a UI feature. |

Everything else in this FG is independent of the answer.

## Reuse map

- **Backend:** `hermes_cli/members.py` (`MemberService`, `GoTrueAdminClient`,
  `require_member_admin`, `MemberView`), `hermes_cli/web_server.py`
  `/api/comms/members*` + `_comms_resolve_principal` (`allow_as=False` on
  writes), `hermes_cli/access.py` C1/C2 + FG-25 C10, `hermes_cli/changes.py`
  (C5), `interactions.py` (C8).
- **Frontend:** `agent-home/src/components/members/MembersView.tsx` (evolves
  into the Users page), `lib/auth/principal.ts` (`requirePrincipal`,
  `apiClientForRequest`), `components/nav-items.ts`, `components/ui/*`,
  `MobileShell`. Every new component root element carries
  `data-component="<ComponentName>"` (repo convention, 72 files already do).

## Design / approach

### 1. Users page (`/users`, replaces `/members`)

Two audiences, one page, server-rendered with the principal already resolved:

- **Everyone (any enrolled principal):** a read-only **directory** — display
  name, groups, and whether the person is active. Not email, not channel ids,
  not roles-as-management. Rationale: a member of a 500-person org needs to know
  who else exists (to assign a task, to share an item), but the directory must
  not become an address book export. Directory reads are C2-scoped: you see
  people who share at least one group with you, plus everyone if you are
  owner/instance-admin.
- **Owner / instance-admin:** the same list plus management columns (email,
  role, account state, channels) and row actions.

Required at this scale, and absent today:
- **server-side pagination** (page size 50) — `/api/comms/members` gains
  `?limit&offset&q&group&role&active`;
- **search** on display/email and **filter** by group / role / status;
- **fix the N+1**: `PrincipalStore.list_principals()` currently issues one
  `_channels_for()` query **per principal** (501 queries for 500 users). Replace
  with a single grouped query. This is a prerequisite for the page, not a
  nice-to-have.

### 2. Groups CRUD (`/groups`, owner/instance-admin + scoped group admins)

- **Tree view per dimension** (tabs or a dimension selector), lazy-expanding,
  showing member counts.
- **Create / rename / move / delete** a group; **delete is refused while rows
  still reference it** (FG-25 §8) and the UI offers `merge into parent` instead.
- **Membership editor**: add/remove principals, toggle `group_role`
  (admin/member), with a person-picker that searches the directory.
- **`elevation_enabled` toggle** — presented as what it is: *"Group admins may
  read members' private memories. Every such read is recorded in the member's
  own ledger."* Off by default, confirmation dialog, audited.
- **Escalation containment is rendered, not just enforced:** a group admin sees
  only their subtree and simply has no controls for roots or re-parenting. The
  server refuses regardless (FG-25 §7); the UI just avoids offering a button
  that will 403.

### 3. User CRUD + invitation activation

#### 3.1 Create (owner / instance-admin)

Form: **login name (email)**, display name, instance role (`admin`/`member`/
`viewer` — never `owner`), and **group assignment** (multi-select across
dimensions). On submit the server, in order:

1. creates the GoTrue account **with a random unguessable password and
   `banned = true`** — so a created-but-unactivated account cannot be logged
   into, and the temporary-password relay disappears entirely;
2. enrols the principal (existing `create_member` flow, including the GoTrue
   rollback-on-enrolment-failure guard — an account that can log in but has no
   principal would authenticate and then hit the 409);
3. inserts the requested `group_members` rows;
4. mints an invitation and returns the link **once**.

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
  approval-gated CLI path), group membership, activate/deactivate.
- **Delete:** default action is **deactivate** (GoTrue ban) — login stops
  immediately while `owner_user_id` attribution and audit history stay intact
  and correctly attributed. Hard delete is a separate, confirmation-gated
  action that must first answer *what happens to their data* — the dialog
  requires choosing **transfer ownership of their rows to <principal>** or
  **delete their private rows**, because `ON DELETE CASCADE` on
  `channel_identities`/`group_members` does **not** touch their memories, files
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
| **enrolment-level** | add / remove / re-role the `principals` row, group membership | the acting profile only |
| **account-level** | GoTrue ban (deactivate), delete, set/reset password | **every profile the account is enrolled in** |

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
2. **Bulk import.** CSV → (email, display, role, groups) with a dry-run preview,
   per-row validation, and a downloadable file of the generated links. Creating
   500 users one form at a time is not a workflow.
3. **"Who read my data" page** (`/me/access`). The FG-25 audit ledger is only
   meaningful if the subject can actually see it: read entries against *me*, with
   reader, time, query snippet and `via_group_id`. This is the visible half of
   the accountability bargain and should ship **with** elevation, not after it.
4. **Channel linking UI.** `channel_identities` is CLI-only today (and empty in
   production), so every inbound message lands on the owner fallback. A "link
   Telegram/WhatsApp" action — admin-initiated or a self-service pairing code —
   is what actually turns multi-user channels on.
5. **Admin activity view.** Filter the existing C5 change log to identity events
   (created / role changed / group changed / invited / activated / deactivated /
   deleted) so administration is reviewable. Reuses `/api/comms/changes`.
6. **Empty / error / permission states.** A member hitting `/groups` gets a clean
   "you don't have access", never a raw 403 or a blank page.
7. **Seat + cost visibility** (nice-to-have): principal count and per-principal
   interaction/cost from C8, so growth is observable before it is a bill.

Deliberately **not** in this FG: owner transfer (stays approval-gated CLI),
SSO/SCIM provisioning (the right answer at 4-figure headcount; a later FG that
writes the same tables), and per-principal rate/cost quotas (runtime concern,
separate FG).

## Data model

New: `invitations` (above). Everything else reuses `principals`,
`channel_identities`, `groups`/`group_members` (FG-25), `changes` (C5),
`interactions` (C8). RLS `FORCE`d on `invitations`: readable only by the owner
role and `created_by`; **never** exposed through any authenticated list endpoint
in a form containing the token (only `expires_at` / `used_at` / `revoked_at`).

## API surface (additive)

```
GET    /api/comms/members            + ?limit&offset&q&group&role&active   (paginated)
GET    /api/comms/directory                                    (any principal, C2-scoped)
POST   /api/comms/members            + groups[]  → returns { member, invitation_url }
POST   /api/comms/members/{id}/invitation                      (regenerate)
DELETE /api/comms/members/{id}/invitation                      (revoke)
DELETE /api/comms/members/{id}       ?strategy=transfer|purge  (hard delete)
GET/POST/PATCH/DELETE /api/comms/groups[/{id}]
POST   /api/comms/groups/{id}/members        PATCH .../members/{user_id}   DELETE …
POST   /api/comms/groups/{id}/merge
POST   /api/auth/invitations/redeem                            (UNAUTHENTICATED, rate-limited)
GET    /api/comms/access-log                                   (my ledger)
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
- Authority: member/viewer receive 403 on every management endpoint; a group
  admin may manage only their subtree; nobody can create/assign `owner`;
  self-demotion/self-deletion refused; last-admin protection.
- Directory scoping: a member sees only co-group principals; owner sees all —
  asserted against real RLS.
- Pagination/search/filter correctness, and a **query-count assertion** proving
  the N+1 is gone (one query for channels, not one per principal).
- Hard delete: both strategies leave no row with a dangling `owner_user_id`.
- Frontend: vitest for the Users/Groups views (states, permission gating,
  optimistic-update rollback on 403), `data-component` present on new roots,
  lint/typecheck/build green.
- E2E: admin creates a user with groups → link generated → **link expires** →
  regenerate → invitee activates and sets a password → logs in → sees exactly
  their groups' rows → an admin elevation read appears in the invitee's
  `/me/access` ledger.

## System testing (system-test box)

On `hermes-systest` against `app_dev`, from a real phone/browser: create a user
with two groups; confirm the 5-minute expiry against the wall clock; regenerate;
activate from a second device; confirm the new user's visibility matches the
FG-25 matrix; confirm deactivate blocks login immediately.

## Dependencies

- **Blocked by:** **FG-25** (groups must exist before they can be managed),
  FG-20 (agent-home BFF), FG-01 (C1/C2), FG-12 (C5), FG-16 (C8).
- **Related:** FG-24 (independent), FG-17 (operator dashboard — not duplicated
  here; this is the `agent-home` surface).
- **Blocks:** any real multi-user rollout.

## Definition of Done

Users + Groups pages shipped with pagination/search/filter and permission-gated
actions; group CRUD + membership + `elevation_enabled` with containment;
invitation create/regenerate/revoke/redeem with hashed single-use tokens,
5-minute default TTL, rate limiting and full audit; accounts banned until
activation; self-service reset; bulk CSV import; `/me/access` ledger; channel
linking; N+1 removed with a query-count test; all backend + frontend tests
green; `scripts/run_tests.sh`, `ruff`, `ty`, `npm run lint`, `tsc`, `next build`
clean; system test passed.

## Progress checklist

- [ ] Resolve the "assign profile" open decision (A / B / C)
- [ ] `list_principals` N+1 fix + paginated/searchable `/api/comms/members`
- [ ] `/users` page: directory (all) + management (owner/admin), pagination, search, filters
- [ ] `/groups` page: per-dimension tree, CRUD, membership, group-admin, `elevation_enabled`, merge
- [ ] `invitations` table + mint/regenerate/revoke + hashed single-use tokens + rate limiting + RLS
- [ ] Create-user flow: banned GoTrue account → principal → groups → invitation link (shown once)
- [ ] `/activate/<token>` page + unauthenticated `redeem` endpoint + password policy + audit
- [ ] Update / deactivate / hard-delete (transfer|purge) + self-protection + last-admin guard
- [ ] Self-service password reset (recovery token)
- [ ] Bulk CSV import with dry-run + generated-links export
- [ ] `/me/access` ledger view (ships with FG-25 elevation)
- [ ] Channel-linking UI
- [ ] Admin activity view over C5
- [ ] Tests (invitation security, authority, directory RLS, pagination/query-count, delete strategies, frontend, E2E)
- [ ] System test on `hermes-systest` passed

## Audit log

| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-08-10 | 1 | devin (for Leo) | Created FG doc | Leo's UI requirements: a Users page scoped by access, owner/admin CRUD over groups and users, and creation that assigns groups + login name and issues a **5-minute invitation link** so the new user sets their own password. Replaces the current browser-generated temporary-password relay in `MembersView.tsx`. Chose an own `invitations` table over GoTrue `admin/generate_link` (global-only expiry, email-oriented, no single-use/revoke/audit); accounts are created **banned** so an unactivated account cannot be logged into; hard delete must resolve data ownership because cascades do not reach memories/files/GTS. Flagged **"assign profile" as an open decision** — a profile is an isolated brain, not a user attribute, so the requirement as stated cannot be implemented without cross-profile identity. |

## Cloud-agent prompt

> **[Phase-6 Wave B — starts after FG-25 merges]** Repo
> `leolau/ai-prentice-4-all`, branch off `develop`. Read
> `docs/design/master-plan/README.md`, `AGENTS.md`, FG-25, and this doc (FG-26).
> Build the **Users & Groups admin console** on `agent-home` plus **invitation
> activation**. Follow the FG-20 BFF pattern — route handlers hold the C1
> principal and proxy to the Python `/api/*`; the browser never sees the
> service-role key; BFF checks are UX only and Python re-checks everything.
> Replace `/members` with `/users`: a C2-scoped **directory** for every
> principal plus management columns/actions for owner/instance-admin, with
> **server-side pagination, search and filters** — and first fix the
> `PrincipalStore.list_principals()` **N+1** (one `_channels_for()` query per
> principal) with a single grouped query, asserted by a query-count test. Add
> `/groups`: per-dimension tree, create/rename/move/delete (delete refused while
> referenced — offer merge), membership + `group_role` editing, and the
> `elevation_enabled` toggle with an explicit confirmation explaining that every
> such read lands in the member's own ledger. Render FG-25 containment (a group
> admin sees only their subtree); the server refuses regardless. **User
> creation:** create the GoTrue account **banned, with a random password** (never
> relay an admin-chosen password — delete the browser-side `generatePassword`
> path), enrol the principal (keep the existing rollback-on-enrolment-failure
> guard), insert the group rows, then mint an **invitation**: 32-byte
> `secrets.token_urlsafe`, **stored only as a SHA-256 hash**, single-use,
> revocable, **5-minute default TTL** (`config.yaml`, not an env var), shown to
> the admin exactly once, with a **Regenerate** action. Add
> `/activate/<token>` + an **unauthenticated, rate-limited**
> `POST /api/auth/invitations/redeem` that re-validates in constant time, sets
> the password via the admin API, **unbans**, marks used, and audits (C5+C8);
> every failure returns an identical neutral response. Deactivate is the default
> "delete"; hard delete must require a `transfer|purge` strategy for the user's
> rows (cascades do **not** reach memories/files/GTS). Add self-service password
> reset, CSV bulk import with dry-run, the `/me/access` ledger view, channel
> linking, and an identity-events view over C5. Enforce self-protection
> (no self-demotion/self-deletion, last-admin guard) and `allow_as=False` on
> every write. New component roots carry `data-component="<ComponentName>"`.
> Tests per this doc, including invitation-security negatives against **real
> GoTrue** and directory scoping against **real RLS**. Run
> `scripts/run_tests.sh`, `ruff`, `ty`, and the agent-home lint/typecheck/build.
> **Ask Leo to resolve the "assign profile" open decision before building the
> create-user form.** Edit ONLY this FG doc. Open a PR linking this doc.
