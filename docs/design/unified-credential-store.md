# Unified Credential Store

> Status: **approved design, in implementation** (2026-08-25).
> Implementation split: PR0 this doc → PR1 store+API → PR2 agent-home UI →
> PR3 skill refactor+migration → PR4 pollers → PR5 cleanup.

## 1. Problem

The box accumulated **three disconnected Google credential models**, none per-user:

| Consumer | Credential | Storage |
|---|---|---|
| `google-workspace` skill | OAuth authorized_user | `$HERMES_HOME/google_token.json` (single account) |
| `custom/calendar/calendar_poller.py` | OAuth refresh token | `GCAL_*` env vars **or** `$HERMES_HOME/google-workspace/credentials/<email>.json` |
| `custom/email/email_poller.py` | Gmail **app password** (IMAP) | `EMAIL*_PASSWORD` in `/opt/data/hermes-messaging.env` |

Symptom that triggered this design: the agent's skill sees an empty store and asks
the user to configure Google again, even though email/calendar work. Meanwhile the
system ships **multi-user login** (see `MULTI_USER_HANDOFF.md`, FG-01) and every
other data domain (todos, incomings, media) is already per-principal — credentials
were the last single-user island.

## 2. Requirements

- **R1** One OAuth credential set per Google account; OAuth everywhere (email
  poller migrates off app passwords to IMAP XOAUTH2).
- **R2** Each login (principal) can create and manage **its own** credentials.
- **R3** The store is **generic** — provider/kind-agnostic (Google OAuth2 first;
  Telegram bot tokens, WhatsApp sessions, passwords supported by design).
- **R4** **Supabase is the source of truth** on deployments that have it; a file
  backend remains as the portable fallback (upstream skill users without Supabase).
- **R5** Background pollers consume an entry **only when its owner opted it in**
  via explicit `services` flags (`email`, `calendar`, `workspace`).
- **R6** Per-user management UI = agent-home Settings → *Connected accounts*.
  The dashboard Keys page keeps managing profile-level service credentials.
- **R7** This design doc is saved before any code.

## 3. Identity & visibility model (reused, not invented)

Principals come from the shipped multi-user system:
`{user_id, display, role ∈ owner|admin|member|viewer, channels[], is_owner}`
(`agent-home/src/lib/auth/principal.ts`, `hermes_cli/access.py`).

Visibility follows the **C2 contract** used by todos/incomings:
`visibility ∈ {'shared'} ∪ {'private:<user_id>'}`; rows carry `owner_user_id`;
**owner sees all**; enforcement is Postgres RLS reading the transaction-local GUCs
`hermes.principal_id` / `hermes.principal_role`, installed by
`hermes_cli/access.py::apply_scope_rls(conn, table)` and bound per transaction by
`bind_principal()`. The credential table gets exactly this — no new ACL machinery.

DB access chain (canonical, reused): `hermes_cli.config.load_config()` →
`hermes_cli.datastore.get_store("supabase-app", "prod", config=...)` → asyncpg with
`search_path` pinned to `app_prod[_profile]`. DDL self-applies lazily on first API
hit via a store `initialize()` (pattern: `hermes_cli/todo_store.py`).

## 4. Schema

Created by `CredentialStore.initialize()` in the C3 prod schema:

```sql
CREATE TABLE IF NOT EXISTS credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    provider TEXT NOT NULL,           -- 'google' today; registry-driven
    name TEXT NOT NULL,               -- account handle (Google: the email address)
    kind TEXT NOT NULL,               -- 'google-oauth2'; registry-driven
    visibility TEXT NOT NULL DEFAULT 'shared'
        CHECK (visibility = 'shared' OR visibility LIKE 'private:%'),
    services TEXT[] NOT NULL DEFAULT '{}',  -- opt-in: email, calendar, workspace
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_user_id, provider, name)
);
CREATE INDEX IF NOT EXISTS credentials_service_idx
    ON credentials USING GIN (services);
```

`apply_scope_rls(conn, 'credentials')` installs the FORCE'd SELECT policy.
Write gating is at the API/store layer (same contract as todos).

### 4.1 Payload protection decision

Plaintext `jsonb` behind FORCE'd RLS — **the same trust model as todos/incomings,
which already store sensitive content**. Rejected pgcrypto PGP encryption for now:
it adds a key-management surface the box doesn't have, defeats jsonb queries, and
the Python API remains the oracle either way. Compensating controls: secret fields
are **redacted in every HTTP response** (`redact(payload, kind)`), TLS in transit,
non-BYPASSRLS app role, and the agent-facing safety denylists (§8). Per-row
AES-GCM is a documented future option.

## 5. Kind registry (R3)

`hermes_cli/credential_store.py::CREDENTIAL_KINDS: dict[str, KindSpec]`:

| kind | payload keys | secret_fields (redacted) | status |
|---|---|---|---|
| `google-oauth2` | `client_id, client_secret, refresh_token, token_uri` (+ optional `token, expiry, scopes`) | client_secret, refresh_token, token | implemented |
| `telegram-token` | `bot_token` | bot_token | reserved (validates by design) |
| `whatsapp-session` | `session` | session | reserved |
| `password` | `username, password` | password | reserved |

`validate_payload(kind, payload)` runs on every `put`, both backends. Adding a kind
is a data change (schema + secret_fields), not a code path change.

## 6. Store module — `hermes_cli/credential_store.py`

Backends:
- `SupabaseCredentialStore` — asyncpg, `bind_principal` per transaction,
  `initialize()` chains `initialize_access` → DDL → `apply_scope_rls`.
- `FileCredentialStore` — portable fallback; layout
  `$HERMES_HOME/credentials/<user_id>/<provider>/<name>.json`, files 0600, dirs 0700,
  atomic writes (temp in same dir + `os.replace`). Owner "sees all" = walk all user
  dirs when the principal is owner.

Selection: `default_credential_store(config=None)` honours
`credentials.backend: auto|supabase|file` in `config.yaml`
(`auto` = Supabase when the app-store DSN resolves, else file).

Surface (principal-bound; HTTP responses always redacted):
`list`, `get`, `put`, `patch(services/visibility)`, `delete`,
`resolve_for_service(service, provider='google')` (owner-bound, **full payloads**,
in-process callers only — pollers, skill, mount materializer), and
`update_tokens(provider, name, owner_user_id, old_refresh_token, fragment)` —
a **conditional single-writer update** (`payload->>'refresh_token' = $old`); the
loser re-reads. Pollers never write: they call only read paths + `update_tokens`
for refresh persistence (the one sanctioned write, race-safe by construction).

## 7. OAuth2 flow (Google adapter)

Mechanics live in `hermes_cli/google_oauth.py` (extracted from the skill's proven
`setup.py`): PKCE, `redirect_uri=http://localhost:1`, `access_type=offline`,
`prompt=consent`, `login_hint=<email>`, pending state in
`$HERMES_HOME/credentials-pending/<user_id>/google.json` (0600, 10-min TTL),
code-or-full-redirect-URL accepted, granted (possibly partial) scopes persisted.

Scopes are **derived from the requested services** (`SCOPES_BY_SERVICE`):

| service | scopes |
|---|---|
| `email` | `https://mail.google.com/` (required for IMAP/SMTP XOAUTH2; `gmail.*` do NOT grant IMAP) |
| `calendar` | `https://www.googleapis.com/auth/calendar` |
| `workspace` | the skill's existing 8 scopes (gmail.readonly/send/modify, calendar, drive, contacts.readonly, spreadsheets, documents) |

Account email is fixed at exchange time via
`https://openidconnect.googleapis.com/v1/userinfo`. **Re-consent is required** when
adding `email` to an existing consent — existing grants lack `mail.google.com`.

HTTP surface: `hermes_cli/credentials_api.py` router `/api/credentials`
(GET list/detail redacted; PUT; PATCH toggles; DELETE; `google/start`;
`google/complete`; `google/{name}/refresh`), mounted in `web_server.py` with the
same 2-line pattern as `todos_api`. Every route gated by
`_comms_resolve_principal` (session principal; no spoofable loopback user header —
in-process consumers cover services; a future HTTP service caller belongs on the
`dashboard_auth/token_auth` provider seam).

New entries default `visibility = private:<actor>`; `shared` is a deliberate toggle.

## 8. Safety denylists

- `agent/file_safety.py`: read-block dir prefixes `credentials/` and
  `credentials-materialized/` (mcp-tokens pattern); same in
  `build_write_denied_prefixes`; `google_token.json` added to the exact-file
  read-block while it exists.
- `gateway/platforms/base.py::_media_delivery_denied_paths`: add both dirs.

## 9. Sandbox mounts

Skill frontmatter can't name per-user paths statically. Where skills activate,
`credential_store.materialize_for_mounts(principal)` runs: Supabase backend writes
each resolvable entry 0600 under
`$HERMES_HOME/credentials-materialized/<user_id>/<provider>/<name>.json` and
registers it via `tools/credential_files.register_credential_file` (containment
check passes — inside HERMES_HOME); file backend registers real files directly.
Cleanup on `clear_credential_files()` / atexit. Members mount own + shared;
owners mount own + shared (+ private rows they can read per RLS).

## 10. Poller consumption (R1, R5)

`custom/google_oauth.py` (poller-side helper): `google_accounts(service)` =
`resolve_for_service(service)` filtered to the opt-in flag (legacy workspace-file
fallback retained until PR5); access tokens cached in memory (2-min early-expiry
margin); refresh persists via `update_tokens` (single writer).

- **email poller**: accounts = `config.json` accounts ∩ store entries with `email`
  service (config keeps host/port/folders/label; the secret moves to the store).
  Auth = `imaplib.IMAP4_SSL` + `authenticate("XOAUTH2", ...)` with
  `xoauth2_string(email, token)`; one retry on auth failure, then per-account
  error status + health field.
- **calendar poller**: accounts derived from store entries with `calendar` service,
  joined to the SQLite `calendar_accounts` sync state by email; `GCAL_*` env path
  removed.

Health endpoints gain `accounts_source: "credential-store"` and per-account
`auth` / `last_auth_error` (schema-compatible additions).

## 11. Migration

- Box script `custom/migrations/migrate_credentials_to_supabase.py` (idempotent,
  owner-bound via `PrincipalStore.get_owner()`): imports
  `google-workspace/credentials/<email>.json` → services `[calendar]` (+`email`
  only when the stored scopes include `mail.google.com`), legacy `google_token.json`
  (userinfo probe for the name), GCAL env tokens; skips on unique-conflict;
  **leaves legacy files in place** until PR5.
- `setup.py --migrate-legacy` does the same for file-backend (upstream) users.
- Rollback safety: PR4 keeps legacy fallbacks; revert + restart restores old
  behavior; table/rows are additive.

## 12. PR split & runbook

| PR | Content | Gate |
|---|---|---|
| PR0 | this doc | — |
| PR1 | store + google_oauth + credentials_api + web_server 2-line mount (PAT push) + tests | — |
| PR2 | agent-home BFF routes + ConnectedAccounts.tsx + vitest | PR1 deployed |
| PR3 | skill thin-over-store + --migrate-legacy + SKILL.md + denylists + materialization + box migration script | — |
| PR4 | pollers (XOAUTH2, store-driven) + custom tests | re-consent done per account |
| PR5 | delete calendar_auth.py, fallbacks, legacy files, env vars, app passwords; docs | ≥24h green |

Box runbook (short commands; see plan file for the exact lines): probe backend →
deploy PR1-3 → run migration script → per-account re-consent in agent-home Settings
(email accounts MUST grant `mail.google.com`) → deploy PR4 → restart pollers →
verify health + 24h → PR5.

## 13. Verification

- Two-principal probe: member sees only own entries; owner sees all.
- `services` toggle off → poller drops the account within one poll cycle.
- Kill a poller mid-refresh → conditional update loses no token.
- Materialized mount file 0600 during a session, gone after reset.
- Agent `cat`/`read_file` on any `credentials*` path refused.
- File-backend unit tests pass with no DSN (upstream parity).
- 24h: mail/calendar rows flowing; no `[AUTHENTICATIONFAILED]` in poller logs.

## 14. Risks

| Risk | Mitigation |
|---|---|
| RLS/GUC trust model | identical to shipped todos; transaction-local binding |
| Refresh-token rotation / dual-backend fork | conditional single-writer update; legacy files unread after PR4, deleted PR5 |
| Skill portability | stdlib/file fallback kept and tested |
| `DATABASE_URL` missing from poller env | runbook probe; file backend covers the gap |
| Wrong Google account on a shared browser | `complete` displays granted email + scopes before saving |
