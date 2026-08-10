---
title: "impl: unified Incomings inbox — step-by-step build plan"
status: draft — ready to build, pending the four open questions
date: 2026-08-10
type: feature
target_repo: ai-prentice-4-all
depends_on: docs/plans/2026-08-10-001-unified-incomings-inbox-plan.md (spec, merged in #166)
origin: user request — "summarize the Inbox scope and create a detailed implementation plan for it"
---

# Unified Incomings inbox — implementation plan

The design rationale lives in the spec (`2026-08-10-001`, merged in #166). This
document is the build: what each PR contains, which file each change lands in,
what proves it works, and what must be decided before it starts.

## Scope, in one page

**The problem.** `/inbox` shows FG-10 approvals and the FG-12 change log —
nothing else. WhatsApp messages, emails and calendar events do arrive and are
triaged, but they live in the pipeline's SQLite
(`/opt/data/whatsapp-messages/whatsapp_data.db`) reachable only by two MCP
servers and the Telegram digest. There is no scoped record in the shared
datastore and no HTTP surface, so no web UI can list them.

**The build.** One new RLS-scoped Postgres table (`inbound_items`), written
best-effort from the two existing arrival chokepoints, read through a new
FastAPI router that mirrors `hermes_cli/files_api.py`, surfaced as a third —
and default — tab on `/inbox` with search, filters, and a linkable detail
route. Two foreign keys give the requested links: `file_assets.inbound_item_id`
(item ⇄ its attachments) and `inbound_items.document_id` (item → what was
remembered from it).

**In scope:** WhatsApp, email, calendar, plus every gateway channel that
already flows through `gateway/inbound.py` — the schema is surface-agnostic.
Search (filters + Postgres FTS + a substring fallback), an item detail view,
both link directions, a backfill of existing history, and a
`hermes incomings remember` CLI.

**Out of scope, deliberately:** replying or composing from the page; changing
how triage classifies anything; copying HTML email bodies into the shared
store; any new *core model tool*; fixing the empty Approvals tab (tracked as a
follow-up in the spec, §7); a to-do structure (see
`docs/design/task-and-todo-structures-inventory.md`).

**Shape of the work:** 5 PRs against `develop`, ~2 build sessions plus a
verification pass on the systest box. Steps 1–2 are backend-only and can land
before any UI exists, so that by the time step 4 renders a list there is real
data in it.

**The pattern being copied.** Every piece of this has a working analogue in the
inbound file registry that shipped in `a22686f49`. The table copies
`hermes_cli/file_registry.py`; the router copies `hermes_cli/files_api.py`; the
pipeline hook copies `custom/shared/file_registration.py`; the gateway hook
copies `gateway/inbound_files.py`; the BFF routes copy
`agent-home/src/app/api/files/`; the page copies
`agent-home/src/components/files/FilesView.tsx`. Read the analogue before
writing each piece — "same as files, but for messages" is the whole design, and
a divergence from it should be deliberate and explained, not incidental.

## Prerequisites — decide before step 1

| # | question | why it blocks | proposal |
|---|---|---|---|
| P1 | CJK search | `to_tsvector('simple', …)` does not segment Chinese; a zh-Hant body becomes one lexeme per character run and FTS under-matches. Fixing it later means a schema change to the generated column. | Ship `'simple'` + the substring fallback, measure on real data, and treat `pg_bigm`/`zhparser` as a follow-up deployment decision. **Decide now that the column is `simple`**, so step 1 is not rewritten. |
| P2 | one owner or per-member? | `custom/shared/file_registration.py` resolves *the* owner principal. If a member is ever to have their own mailbox, `account_id → principal` needs a mapping table, which changes the registration signature. | Keep the owner binding; add a `TODO` at the seam. Revisit when a second member gets a channel. |
| P3 | volume | If email is thousands/day, `OFFSET` pagination degrades and "everything" is the wrong default view. | Count rows in the pipeline SQLite first (`SELECT surface, count(*) …`), one query, before step 3. If >50k, use keyset pagination on `(occurred_at, id)` from the start. |
| P4 | escalations: chip or tab? | Affects step 4's UI only, not the schema. | A chip. Defer. |

P1 and P3 are the ones that cost rework; P2 and P4 do not block.

## Step 1 — the table and the registry

**PR:** `feat(incomings): inbound_items registry with C2 scoping`
**Files:** new `hermes_cli/inbound_registry.py`; new
`tests/hermes_cli/test_inbound_registry_e2e.py`; add
`"hermes_cli/inbound_registry.py"` to the core-boundary list in
`agent/core_boundary.py`.

Model the module on `hermes_cli/file_registry.py` — same module layout, same
helper imports from `hermes_cli.access`, same `default_registry()` factory
resolving the store through `get_store("supabase-app", mode, config=config)`
and reading `_role_reads_configured(config)` so incomings, files and memories
agree about downward role reads.

```python
INBOUND_ITEMS_TABLE = "inbound_items"
GRANT_ITEM_KIND = "inbound"     # a new kind — unlike files, an item is not a document
ITEM_KINDS = ("message", "email", "event")
MAX_BODY_CHARS = 64_000

@dataclass
class InboundItem: ...          # mirrors FileAsset, with .as_dict()

class InboundRegistry:
    async def initialize(...)                     # SCHEMA_SQL + apply_scope_rls, idempotent
    async def upsert(principal, *, surface, external_id, kind, occurred_at, ...) -> InboundItem
    async def get(principal, item_id) -> InboundItem | None
    async def list(principal, *, query="", surfaces=(), kinds=(), contact=None,
                   since=None, until=None, importance=None, remembered=None,
                   has_attachments=None, limit=50, offset=0) -> tuple[list[InboundItem], int]
    async def facets(principal) -> dict           # surface/kind/sender counts
    async def mark_remembered(principal, item_id, *, document_id, by) -> InboundItem
    async def attachments(principal, item_id) -> list[FileAsset]
```

The DDL is in the spec (§1) and goes in verbatim as `SCHEMA_SQL`. Three points
the implementation must not soften:

- **`upsert`, not `register`.** `ON CONFLICT (owner_user_id, surface,
  account_id, external_id) DO UPDATE` — a re-polled IMAP UID or a re-synced
  calendar event is the same arrival. Preserve `received_at` and
  `document_id`/`remembered_at` on conflict (only the *content* fields and the
  triage mirror update); a re-poll must never un-remember something.
- **Truncate `body` to `MAX_BODY_CHARS`** in the registry, not at the call
  sites, so no producer can bypass it. Compute `excerpt` there too.
- **RLS is not optional.** `apply_scope_rls(conn, INBOUND_ITEMS_TABLE,
  grant_item_kind=GRANT_ITEM_KIND, role_elevation=self.role_reads)` in
  `initialize()`, and every read binds the principal inside the transaction.
  Email bodies are the most sensitive rows in the system.

**Tests** (`test_inbound_registry_e2e.py`, modelled on
`test_file_registry_e2e.py`, real Postgres in Docker):
owner reads own; a member cannot read another member's; owner-role elevation is
labelled; upsert on the same `external_id` updates one row rather than
inserting a second; a rescheduled event moves `starts_at` in place; a re-poll
after `mark_remembered` keeps `document_id`; an oversized body is truncated;
FTS matches a body word and a filter narrows.

**Done when:** the tests pass and `initialize()` is idempotent against a schema
that already has the table.

## Step 2 — writing rows: the two hooks, the link column, the backfill

**PR:** `feat(incomings): register arrivals from the pipeline and the gateway`
**Files:** new `custom/shared/inbound_registration.py`; edits to
`custom/email/email_poller.py`, `custom/whatsapp/batcher.py`,
`custom/calendar/calendar_poller.py`, the three triage agents; new
`gateway/inbound_items.py` + a call in `gateway/inbound.py`; a migration adding
`file_assets.inbound_item_id`; new `hermes_cli/incomings_backfill.py` +
`hermes incomings backfill` wiring.

### 2a. The pipeline hook

`custom/shared/inbound_registration.py` is a near-copy of
`file_registration.py`: same owner-principal resolution with the 60 s retry
guard (`_OWNER_RETRY_INTERVAL_SEC`), same "returns `False`, never raises"
contract, same `asyncio` bridge for sync callers.

```python
def register_item(*, surface, external_id, kind, occurred_at, account_id=None,
                  conversation=None, sender_id=None, sender_name=None,
                  contact_id=None, title=None, body=None, starts_at=None,
                  ends_at=None, has_attachments=False, metadata=None,
                  importance=None, triaged=False) -> bool
```

Call sites — each is one call next to an existing SQLite insert:

| file | insert | notes |
|---|---|---|
| `custom/whatsapp/batcher.py` `process_message()` | `INSERT OR IGNORE INTO messages` (~line 246) | `external_id=msg_id`, `account_id=source_phone`, `conversation=chat_id`, `kind="message"`, `occurred_at=timestamp`. Put the call **after** `db.commit()` and after the `IntegrityError` early-return, so a duplicate WhatsApp message does not re-register. `has_attachments` is known from `media_type`. |
| `custom/email/email_poller.py` | `INSERT OR IGNORE INTO email_messages` (~line 315) | `external_id=message_id` (the RFC one), `account_id`, `conversation=thread_id or message_id`, `kind="email"`, `title=subject`, `body=text_body` (never `html_body`), `metadata={"to": to_addrs, "cc": cc_addrs, "folder": folder}`. Sits beside the existing `_register_email_attachments(...)` call, which already does exactly this for files. |
| `custom/calendar/calendar_poller.py` `sync_events()` | both branches — the `INSERT INTO calendar_events` (~line 277) **and** the update branch above it | `external_id=google_event_id`, `account_id`, `conversation=recurring_event_id or google_event_id`, `kind="event"`, `title=summary`, `body=description`, `starts_at`/`ends_at`, `occurred_at=start_time`, `metadata={"location", "html_link", "conference_link", "status"}`. Registering on the update branch is what makes a rescheduled meeting correct in the inbox. |
| the three triage agents | after classification | a second `register_item` with the same `external_id` (the upsert makes this cheap) carrying `importance` + `triaged=True`. |

The `.mjs` WhatsApp batcher variant reaches Python through the same path it
already uses for media registration; no separate JS implementation.

### 2b. The gateway hook

`gateway/inbound_items.py`, beside `gateway/inbound_files.py`, reusing that
module's `surface_for()` alias map (`bluebubbles→imessage`, `api_server→api`,
`local→cli`) and `_principal_for(source)` rather than re-deriving either. One
call from the same place in `gateway/inbound.py` where `register_event_files`
is invoked, after caching and before the turn. Same best-effort contract: log
and continue.

### 2c. The attachment link

Migration: `ALTER TABLE file_assets ADD COLUMN IF NOT EXISTS inbound_item_id
UUID REFERENCES inbound_items(id) ON DELETE SET NULL;` plus an index on it.
Both registrations already run at the same chokepoint with the same message id
in hand, so ordering is: register the item first, then pass its id into the
file registration. For the legacy rows, the backfill matches on
`(surface, account_id, message_id)`; anything unmatched keeps today's
behaviour, which is a provenance line rather than a link.

### 2d. The backfill

`hermes incomings backfill [--surface …] [--since …] [--dry-run]` walks
`messages`, `email_messages` and `calendar_events` in the pipeline SQLite and
upserts. Idempotent by construction — the upsert key is the same `external_id`
the pipeline already stores — so the acceptance test is "run it twice, identical
row count". Also stamps `file_assets.inbound_item_id` for the rows it can match.

**Tests:** each hook stamps the right surface/account/sender for a synthetic
payload; a registration failure does not break the message path (patch the
registry to raise, assert the poller still commits); the calendar update branch
moves `starts_at`; backfill is idempotent; a file registered alongside an item
carries `inbound_item_id`.

**Done when:** on the systest box, a real WhatsApp message, a real email with a
PDF, and a real calendar event each produce one correctly-provenanced row
within one poll cycle, and the PDF's `file_assets` row points at its item.

## Step 3 — the API

**PR:** `feat(incomings): read API and agent-home BFF routes`
**Files:** new `hermes_cli/incomings_api.py`; two lines in
`hermes_cli/web_server.py` (import at ~line 275, `include_router` at ~line 278,
beside `_files_router`); new
`agent-home/src/app/api/incomings/{route.ts,facets/route.ts,[id]/route.ts,[id]/remember/route.ts}`;
client methods + types in `agent-home/src/lib/api/client.ts` and
`agent-home/src/types/index.ts`.

`hermes_cli/incomings_api.py` follows `files_api.py` line for line:
`APIRouter(prefix="/api/registry/incomings")`, principal via
`_comms_resolve_principal(request, allow_as=True)` (lazy-imported to avoid the
circular import), an `_ensure_table()` probe returning an empty page rather
than a 500 on a box that has never received anything, and `limit` capped at
200.

| endpoint | returns |
|---|---|
| `GET ""` | `{items, total, limit, offset}` — params `q`, `surface` (csv), `kind`, `contact`, `from`, `to`, `importance`, `remembered`, `has_attachments`, `limit`, `offset` |
| `GET "/facets"` | `{surfaces: [...], kinds: [...], senders: [...]}` with counts, so a chip is never offered for a surface with no rows |
| `GET "/{item_id}"` | the item + `attachments[]` (from `file_assets`) + its `memory` link; 404 for both absent and invisible, so a 403 cannot confirm someone else's item exists |
| `POST "/{item_id}/remember"` | ingest + stamp; returns the updated item |

Search is one query with three layers (spec §3): filter predicates, then
`websearch_to_tsquery('simple', $q)` against `search_tsv` ranked
`ts_rank_cd(...) DESC, occurred_at DESC`, then — only when the tsquery matches
nothing and `len(q) <= 24` — an `ILIKE` fallback across
`sender_id`/`sender_name`/`title` for phone-number and address fragments.

The BFF routes are copies of `agent-home/src/app/api/files/route.ts`: resolve
the principal, forward under the bridged token, map `HermesApiError` to its
status and anything else to a 502. Note the prefix asymmetry that already
caught us once and is commented in `files_api.py` — the browser path is
`/api/incomings/*`, the Python path is `/api/registry/incomings/*`, because
`/api/files` on the Python side is the dashboard's filesystem browser.

**Tests:** `client.incomings.test.ts` for URL construction and token replay
(copy `client.b2.test.ts`); Python tests for the empty-table path, the
visibility 404, the FTS/fallback branch, and the facets counts.

## Step 4 — the UI

**PR:** `feat(agent-home): Incomings tab and item detail on /inbox`
**Files:** `agent-home/src/components/inbox/InboxView.tsx` (a third tab), new
`components/inbox/{IncomingsList,IncomingRow,IncomingsFilters}.tsx`, new
`src/app/inbox/[id]/page.tsx`, edits to `src/app/inbox/page.tsx` and
`src/components/nav-items.ts`.

`InboxView` already owns a segmented switch, a busy/notice/error strip and the
BFF-forwarding pattern; this widens `type Tab` to
`"incomings" | "approvals" | "changes"`, defaults to `incomings`, and leaves
the other two branches untouched. Every new component's root element carries
`data-component="…"` per the repo standard.

- **Row:** glyph by surface · title-or-excerpt · right-aligned relative time;
  second line `surface · sender · 📎 n · importance · ◇ when remembered`.
  Calendar rows show the time range instead of a relative timestamp.
- **Controls:** debounced search box, surface chips from `/facets`, date range.
  **Filter state lives in the URL** (`/inbox?tab=incomings&q=invoice&surface=email`)
  so a search is linkable and survives reload.
- **Detail** at `/inbox/[id]` — a full route, not a sheet, so `/files`, a
  digest and a Telegram escalation can all link to it. Full body, full
  provenance, attachments as `/files` cards with View/Download via the existing
  signed-URL route, and either a link to `/memory?document=<id>` or a
  **Remember this** action.
- **Nav:** the Inbox hint in `PRIMARY_NAV` changes from "Approvals + changes" to
  "Everything that arrived". No new tab; the phone bottom bar stays at five.
- **`/files` back-link:** the file detail panel gains "arrived in this message"
  → `/inbox/<inbound_item_id>` when the column is set.

**Tests:** extend `InboxView.test.tsx` (existing assertions must still pass);
new `IncomingsList.test.tsx` for rendering and the empty state; a URL
round-trip test for the filters; a BFF test that another principal's item is a
403 through the route. Then `pnpm lint`, `pnpm typecheck`, `pnpm test` in
`agent-home/`.

## Step 5 — remembering

**PR:** `feat(incomings): remember an arrival into the memory tier`
**Files:** `hermes_cli/incomings_api.py` (the POST handler's body), a
`hermes incomings remember <id>` subcommand, and the three triage skills under
`custom/skills/`.

Ingest `title` + `body` as a RAG document with `source_kind = "inbound"` and
`source_ref = <inbound_items.id>`, then stamp `document_id` / `remembered_at` /
`remembered_by`. Same shape as `remember-file` so there is one mental model and
one tested path. The triage skills call it with `remembered_by='<skill>'`
alongside (or instead of) today's `custom/shared/memory_bridge.py` MEMORY.md
append — which is what finally lets a memory be traced back to the message that
produced it, rather than to a free-text provenance tag.

CLI + skill, not a model tool: rung 2 of the footprint ladder, zero per-call
schema cost. The existing MCP servers keep working unchanged against SQLite.

## Verification on the systest box

Follow `.agents/skills/testing-hermes-systest-box/SKILL.md` (no SSH; the access
path is the alibaba-cloud MCP `OOS_RunCommand` tool). The live pass, after step 4:

1. `hermes incomings backfill --dry-run`, then for real; row counts per surface
   match the SQLite counts.
2. Send a WhatsApp message with a photo, an email with a PDF, and create a
   calendar event. Each appears on `/inbox` within one poll cycle with correct
   provenance.
3. Move the calendar event; the row updates rather than duplicating.
4. Open the email's PDF from the item; open the item from `/files`.
5. Search a word from the email body, then a fragment of the sender's number
   (exercises the FTS path and the fallback).
6. Remember the email; it becomes findable on `/memory` and the item shows the
   link.
7. Log in as a synthetic member; none of the owner's items are visible.

Step 7 is the one that must not be skipped. It is the same isolation check the
multi-user rollout ran for memories and files, and this table carries email
bodies.

## Risks

- **RLS regression is the only severe one.** Mitigation: the step-1 test suite
  copies `test_file_registry_e2e.py`'s isolation cases before any writer exists,
  and the live member check gates the release.
- **Poller edits touch running production services.** Mitigation: every hook is
  best-effort and wrapped; the "registry raises → poller still commits" test is
  mandatory; deploy step 2 with the API and UI absent so a bad hook is visible
  in logs with nothing depending on it.
- **CJK under-matching** (P1) — known, accepted, measured after step 4.
- **Volume** (P3) — one counting query before step 3 decides offset vs keyset.
- **Body duplication.** The shared store holds a truncated plain-text copy of
  every email. That is deliberate (it is what makes search work) but it is a
  second place sensitive content lives; it inherits the same RLS and the same
  backup posture as `memories`, and nothing weaker is acceptable.
