---
title: "feat: a unified Incomings inbox — every WhatsApp, email and calendar arrival in one searchable list, linked to its files and its memory"
status: draft — spec for review
date: 2026-08-10
type: feature
target_repo: ai-prentice-4-all
origin: user request — "the inbox page does not show a list of all existing incomings (whatsapp messages, emails, calendars). Why?" → "create a spec to allow user to view all incomings including but not limited to whatsapps, emails, calendars with search capability and links to files and memory"
---

# Unified Incomings inbox

## The problem, precisely

`/inbox` was never an inbox of *messages*. It is the FG-10 approvals queue plus
the FG-12 change log, and nothing else:
`agent-home/src/app/inbox/page.tsx` calls exactly two endpoints
(`GET /api/comms/notifications`, `GET /api/comms/changes`) and
`components/inbox/InboxView.tsx` renders exactly two tabs (Approvals,
Changes). No WhatsApp, email or calendar data source is reachable from that
page — so "why is it empty" has two separate answers, and both matter here:

1. **It shows the wrong thing.** Nothing on that page was ever supposed to list
   arrivals. The name is the whole misunderstanding.
2. **Even the approvals half is empty.** `hermes_cli/human_comms.py` is imported
   by exactly two call sites — the read and the answer endpoint in
   `hermes_cli/web_server.py:3354,3386`. `NotificationStore.create()` is never
   called by production code, so the table is only ever populated by tests.

Meanwhile the arrivals *do* exist, in a place no web surface can reach:

| where | what it holds | who can read it |
|---|---|---|
| `/opt/data/whatsapp-messages/whatsapp_data.db` (SQLite, on the box) | `messages` (WhatsApp), `email_messages`, `calendar_events` + `calendar_attendees`, `unified_contacts`/`contact_handles`, `escalations`, digests | the two MCP servers (ports 8650/8651), the Telegram digest/escalation pushers, and anything with shell access — **no HTTP API, no principal scoping** |
| `file_assets` (Supabase app schema) | every *attachment* that arrived, with provenance + RLS | `/files` in agent-home, via `GET /api/registry/files` |
| `memories` / `rag_documents` | what was deliberately remembered | `/memory` |
| `notifications` (app schema) | approvals/asks — never written | `/inbox` |

So the content layer for Incomings already exists and is already being
triaged; what is missing is a **scoped, queryable record in the shared
datastore** and a surface over it. The file registry (`FG` plan
`2026-08-07-001`) solved the identical problem one level down for attachments,
and this plan deliberately copies its shape rather than inventing a second one.

```
arrival → INCOMINGS REGISTRY (always, automatic, provenance, RLS)  → /inbox
              │              │
              │              └─ attachments → file_assets  (already exists)
              │
              └─ explicit ask, or a triage skill's decision
                              ↓
                     MEMORY / RAG (opt-in)                          → /memory
```

Registering an arrival is a fact. Triage judgements (importance, tasks,
escalation) and memory ingestion stay what they are today: separate, later,
and reversible.

## Scope

**In:** WhatsApp messages, email messages, calendar events, and every gateway
channel that already flows through `gateway/inbound.py` (Telegram, Discord,
Slack, Signal, SMS, iMessage, …) — the design is surface-agnostic, "including
but not limited to" is a schema property, not a list of special cases. Search,
filters, an item detail view, and two-way links to `/files` and `/memory`.

**Out (explicitly):** replying/sending from the page (read + triage only in
this plan; composing is a separate FG); changing how triage classifies
anything; a second copy of the raw email bodies for archival; any new *core
model tool* (footprint ladder — this is a CLI command + a skill + existing MCP
servers).

## 1. The registry

### Table `inbound_items` (app schema, beside `file_assets` / `memories`)

```sql
CREATE TABLE IF NOT EXISTS inbound_items (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id  TEXT NOT NULL,            -- C1 principal, from the channel binding
    visibility     TEXT NOT NULL,            -- private:<owner> by default

    -- provenance: which of my inboxes, from whom, when
    surface        TEXT NOT NULL,            -- whatsapp | email | calendar | telegram | agent_home | …
    account_id     TEXT,                     -- the receiving number / mailbox / calendar id
    conversation   TEXT,                     -- chat id, mail thread id, recurring-event id
    external_id    TEXT NOT NULL,            -- the channel's own id (WA msg id, RFC Message-ID, google_event_id)
    sender_id      TEXT,                     -- raw handle (+85290000000, ada@example.com)
    sender_name    TEXT,
    contact_id     TEXT,                     -- unified_contacts.id when the pipeline resolved one
    occurred_at    TIMESTAMPTZ NOT NULL,     -- when it was sent / when the event starts
    received_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- the item itself, normalised to one shape across surfaces
    kind           TEXT NOT NULL,            -- message | email | event
    title          TEXT,                     -- subject / event summary / null for a chat line
    body           TEXT,                     -- text body, plain text only (never HTML)
    excerpt        TEXT,                     -- first ~280 chars, what the list renders
    starts_at      TIMESTAMPTZ,              -- events only
    ends_at        TIMESTAMPTZ,
    has_attachments BOOLEAN NOT NULL DEFAULT FALSE,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- surface extras: location, cc, html_link, is_group…

    -- triage, mirrored from the pipeline (display only; the pipeline stays authoritative)
    importance     TEXT,                     -- critical | normal | low | null
    triaged        BOOLEAN NOT NULL DEFAULT FALSE,
    escalated_at   TIMESTAMPTZ,

    -- the memory link, when one exists (same shape as file_assets)
    document_id    UUID REFERENCES rag_documents(id) ON DELETE SET NULL,
    remembered_at  TIMESTAMPTZ,
    remembered_by  TEXT,                     -- 'user' | '<skill name>'

    search_tsv     tsvector GENERATED ALWAYS AS (
                       to_tsvector('simple',
                           coalesce(title,'') || ' ' ||
                           coalesce(sender_name,'') || ' ' ||
                           coalesce(sender_id,'') || ' ' ||
                           coalesce(body,''))
                   ) STORED,

    UNIQUE (owner_user_id, surface, account_id, external_id)
);

CREATE INDEX ON inbound_items (owner_user_id, occurred_at DESC);
CREATE INDEX ON inbound_items USING GIN (search_tsv);
CREATE INDEX ON inbound_items (surface, occurred_at DESC);
CREATE INDEX ON inbound_items (contact_id);
```

**One row per arrival, keyed on the channel's own id.** Unlike `file_assets`
(where an arrival is the fact and identical bytes are deduped in the bucket),
an inbound item *has* a stable external identity, and pollers re-see it: IMAP
re-reads a UID, `calendar_events` gets re-synced on every `syncToken` walk with
an updated `status`/time. So the write is an **upsert** on
`(owner_user_id, surface, account_id, external_id)` — re-polling updates the
row (a moved meeting, a retracted message) instead of inventing a second
arrival. A calendar event edited three times is one row with a history in the
pipeline's SQLite, not three lines in the user's list.

`body` is plain text only. `email_messages.body_html` stays in SQLite; copying
HTML into the shared store buys nothing the excerpt and the detail view do not
already give, and it is the part most likely to carry tracking pixels and
remote content we would then have to sanitise before rendering.

RLS uses the existing `scope_filter` / `apply_scope_rls` / `bind_principal`
helpers exactly as `hermes_cli/file_registry.py` does, with the same
`role_reads` elevation and the same item-grant path, so a member sees their own
arrivals and anything shared, and an owner-role read is elevated *and
labelled*. A registry with weaker access than memory would be a way to read
somebody's private material by asking a different table for it — and email
bodies are the most sensitive thing in the system.

### Retention

None by default: the point is that "what did Leo send me last month" is
answerable. `metadata` and `body` are capped at 64 KB per row (truncated with
`…`, full body remains in the pipeline DB and in the mailbox), so a mailing
list with a 2 MB newsletter cannot bloat the shared store.

## 2. Who writes it

Two hook points, mirroring the file registry so the seams are already proven:

1. **The standalone pipeline** (`custom/`). `custom/shared/file_registration.py`
   already exists for exactly this reason — the pollers do not go through the
   gateway's `MessageEvent` path, so it resolves the owner principal from the
   `PrincipalStore` and calls `store_and_register()` directly, best-effort. Add
   a sibling `custom/shared/inbound_registration.py` with the same contract
   (`register_item(...) -> bool`, never raises, 60 s owner-resolution retry) and
   call it from the three write points that already insert into SQLite:
   `custom/email/email_poller.py`, the WhatsApp batcher
   (`custom/whatsapp/batcher.py` — the `.mjs` variant calls the Python entry
   through the same path it already uses for media), and
   `custom/calendar/calendar_poller.py`. The triage agents additionally call it
   once more after classification to stamp `importance` / `triaged`.
2. **The gateway** (`gateway/inbound.py`, beside `register_event_files`). Every
   other channel already funnels through one chokepoint that knows platform,
   `account_id`, sender, conversation and the resolved `internal_user_id`;
   register there, after caching, before the turn.

Both are best-effort and never break a turn: a DB failure logs and the message
proceeds. A missing row is recoverable by backfill; a dropped message is not.

### Attachments: linking to `/files`

`file_assets` gains one nullable column, `inbound_item_id UUID REFERENCES
inbound_items(id) ON DELETE SET NULL`. Both registrations already run at the
same chokepoint with the same message id in hand, so the file hook stamps it
when the item row exists. That is the whole link, in both directions:

- an item shows its attachments (`SELECT … FROM file_assets WHERE
  inbound_item_id = $1`), each row already carrying the signed-URL/View path
  `/api/registry/files/{id}/content`;
- a file's detail panel gains "arrived in this message" → `/inbox/<item id>`,
  which is the provenance answer `/files` currently gives only as flat text.

Where a legacy file row has no `inbound_item_id`, the backfill matches on
`(surface, account_id, message_id)`; unmatched rows keep today's behaviour.

### Backfill

`hermes incomings backfill [--surface …] [--since …]` walks the pipeline SQLite
(`messages`, `email_messages`, `calendar_events`) and upserts rows. Idempotent
by construction — the upsert key is the same `external_id` the pipeline stores,
so re-running is a no-op. Run once at deploy; keep it available for a poller
outage.

## 3. Search

Three layers, cheapest first, all in one query:

- **Filters** (SQL predicates): surface, account, contact/sender, date range,
  `has_attachments`, `remembered`, `importance`, `triaged`. These drive chips,
  and — as with `/files`' `GET /api/registry/files/surfaces` — the chips are
  built from a counts query so a surface with no items is never offered as a
  dead control.
- **Full-text** over `search_tsv` (title + sender + body) with
  `websearch_to_tsquery('simple', …)`, ranked by
  `ts_rank_cd(...) DESC, occurred_at DESC`. `'simple'` rather than `'english'`
  because this corpus is bilingual (zh-Hant/en); a stemmer tuned to one of
  them silently degrades the other. CJK segmentation is a known limitation —
  see Open questions.
- **Substring fallback** when the query has no lexeme match and is short
  (a phone-number fragment, a partial address): `ILIKE` on
  `sender_id`/`sender_name`/`title`. Only as a fallback, so the common path
  stays index-only.

Deliberately *not* semantic/vector search here. The memory tier owns
embeddings; an inbox where "the invoice from last week" returns a fuzzy ranked
guess instead of the arrivals matching the words is the wrong instrument. Items
that were *remembered* are searchable semantically on `/memory`, which is
exactly the division of labour the file registry already draws.

## 4. The API

A new router, `hermes_cli/incomings_api.py`, mounted beside
`hermes_cli/files_api.py` and following it line for line (principal resolution
via `_comms_resolve_principal(request, allow_as=True)`, an `_ensure_table`
probe returning an empty page rather than a 500 on a box that has never
received anything, `limit` capped at 200):

| endpoint | purpose |
|---|---|
| `GET /api/registry/incomings` | `q`, `surface` (csv), `kind`, `contact`, `from`/`to`, `importance`, `remembered`, `has_attachments`, `limit`, `offset` → `{items, total, limit, offset}` |
| `GET /api/registry/incomings/facets` | surfaces + kinds + top senders, with counts (drives the chips) |
| `GET /api/registry/incomings/{id}` | one item, plus its `attachments[]` (from `file_assets`) and `memory` link |
| `POST /api/registry/incomings/{id}/remember` | ingest into RAG, stamp `document_id`/`remembered_at`/`remembered_by` |

Prefix note, same trap as the file registry: `/api/incomings` is free, but the
`/api/registry/*` namespace is where "things that arrived" already live, and
keeping them together means one auth/scoping pattern to review.

agent-home BFF routes mirror them one-for-one under `/api/incomings/*`
(`src/app/api/incomings/{route.ts,facets/route.ts,[id]/{route.ts,remember/route.ts}}`),
each forwarding under the bridged C1 principal and mapping `HermesApiError` to
a status — a copy of `src/app/api/files/route.ts`. Client methods
`incomings()`, `incoming()`, `incomingFacets()`, `rememberIncoming()` on
`HermesApiClient`, with shared types in `src/types/index.ts`.

## 5. The UI

`/inbox` becomes three tabs, with **Incomings first and default**:

```
[ Incomings (128) ] [ Approvals (0) ] [ Changes ]
```

The existing `InboxView` already owns a segmented switch, a busy/notice/error
strip and the BFF-forwarding pattern; this adds a third `Tab` value and an
`IncomingsList`, leaving Approvals and Changes untouched. Per the repo's
component standard every new component's root element carries
`data-component="…"`.

**List row** (mobile-first, one line of provenance under one line of content):

```
✉  Invoice #4021 — Acme Ltd                       14:08
   Email · ada@acme.com → leo@… · 📎 2 · ★ critical
```

Glyph by surface, title-or-excerpt, right-aligned relative time; second line is
surface · sender · attachment count · importance · a ◇ when remembered.
Calendar rows show the time range instead of a relative timestamp.

**Controls:** a search box (debounced, drives `q`), surface chips from
`/facets`, and a date-range control. Filter state lives in the URL query so a
search is linkable and survives a reload — `/inbox?tab=incomings&q=invoice&surface=email`.

**Detail view** (`/inbox/[id]`, a full route so it is linkable from `/files`,
from a digest, and from a Telegram escalation): full body, full provenance,
attachments rendered as `/files` cards with View/Download, and either a link to
the memory document (`/memory?document=<id>`) or a **Remember this** action.

**Nav:** `PRIMARY_NAV`'s Inbox hint changes from "Approvals + changes" to
"Everything that arrived"; no new tab — the phone bottom bar stays at five.

## 6. Remembering an item

Same shape as `remember-file`, so there is one mental model and one tested
path: `hermes incomings remember <id>` ingests title+body as a RAG document
with `source_kind = 'inbound'`, `source_ref = <inbound_items.id>`, then stamps
`document_id` / `remembered_at` / `remembered_by`. The three triage skills
(`custom/skills/{email,whatsapp,calendar}-triage`) call it with
`remembered_by='<skill>'` in place of, or alongside, today's
`custom/shared/memory_bridge.py` MEMORY.md append — so a fact in memory can be
traced back to the message that produced it, which the MEMORY.md provenance tag
can only approximate as free text.

CLI + skill, not a model tool: rung 2 of the footprint ladder, zero per-call
schema cost. The existing MCP servers (`whatsapp_search_messages`,
`email_search`, …) keep working unchanged against SQLite; this plan does not
touch them.

## 7. Approvals, while we are here

Fixing the list does not fix the second finding: nothing calls
`NotificationStore.create()`. Out of scope for this plan, but it should not be
lost — either the escalation pusher (`custom/shared/escalation_pusher_v2.py`,
which already decides "this needs Leo") becomes the first producer of FG-10
notifications, or the Approvals tab is honest that it is unused. Tracked as a
follow-up; this plan neither wires it nor removes it.

## Testing

- **RLS:** owner reads own; a member cannot read another member's items; the
  owner-role elevated read is labelled — the `tests/hermes_cli` pattern used for
  memories, grants and `file_assets`.
- **Upsert identity:** re-registering the same `external_id` updates rather than
  duplicating; a rescheduled calendar event moves `starts_at` on one row; two
  different accounts receiving the same forwarded email are two rows.
- **Best-effort:** a DB/Storage failure during registration logs and the message
  turn still completes.
- **Search:** a filter narrows; FTS matches a body word; the substring fallback
  finds a phone fragment; a query matching nothing returns an empty page, not a
  500; the surface chips list only surfaces with rows.
- **Links:** an item with two attachments shows both, and each file's detail
  links back to the item; a remembered item links to its document, an
  unremembered one offers the action.
- **Backfill:** run twice over the pipeline SQLite → identical row count.
- **Frontend:** tabs switch, Incomings is default, URL query round-trips the
  filters, the detail route refuses another principal's item (403 through the
  BFF), and existing `InboxView.test.tsx` assertions still pass.
- **Live:** a real WhatsApp message, a real email with a PDF and a real calendar
  event all appear on `/inbox` with correct provenance within one poll cycle;
  the PDF opens from the item; remembering it makes it findable on `/memory`.

## Sequencing

Each step is a separate PR against `develop`, rebased before push (several
agents work this repo).

1. `inbound_items` + RLS + `IncomingRegistry` (registry exists, nothing uses it)
2. Pipeline + gateway hooks, `file_assets.inbound_item_id`, backfill command
3. `hermes_cli/incomings_api.py` + BFF routes + client/types
4. `/inbox` Incomings tab + `/inbox/[id]` detail (visible value)
5. `hermes incomings remember` + skill wiring, `/files` → item back-link

Steps 1–2 can land before any UI: once arrivals accumulate, step 4 has real
data to render on day one.

## Decisions taken

1. **Mirror into the app schema; do not serve SQLite over HTTP.** The pipeline
   DB is single-user, unscoped, and lives on one box's disk. Every other
   user-visible tier (memory, files, notifications) is Postgres with C2
   scoping, and `agent-home` reads through RLS by design. Exposing
   `whatsapp_data.db` through a new API would mean re-implementing scoping in a
   second place, for a store that cannot express `visibility` at all. The
   pipeline keeps SQLite as its working store for triage, batching, digests and
   the MCP servers; the registry is the shared, scoped read model.
2. **Upsert on the channel id, not append-per-sighting** — the opposite of
   `file_assets`, and for the opposite reason: an arrival there has no stable
   identity and a re-send is genuinely a second event, while a re-polled email
   UID is the same email.
3. **One `inbound_items` table, not one per surface.** "Including but not
   limited to" is the requirement; a per-surface table means a migration before
   every new channel and a UNION for every search. Surface-specific fields live
   in `metadata` JSONB.
4. **Plain text only in the shared store**; HTML bodies stay in the pipeline DB.
5. **Postgres FTS, not embeddings** — the memory tier owns semantic search.
6. **A third tab on `/inbox`, not a new route** — the user's expectation is that
   the Inbox contains their incomings; moving them elsewhere preserves the
   original confusion.
7. **CLI + skill for remembering**, no new core model tool (footprint ladder).

## Open questions

1. **CJK search.** `'simple'` tokenisation does not segment Chinese, so a
   zh-Hant body is one lexeme per run of characters and FTS will under-match.
   The substring fallback covers short queries. If this bites, the options are
   `pg_bigm`/trigram indexes or `zhparser` on the Supabase instance — both are
   extension installs on a managed box and so a deployment decision, not a code
   one. Proposal: ship with the fallback, measure, decide.
2. **Does the pipeline run under one owner or per member?** `file_registration.py`
   resolves *the* owner principal for a personal agent. If members are ever to
   have their own WhatsApp/mailbox, `account_id → principal` needs a mapping
   table. Proposal: keep the owner binding now, note the seam.
3. **Volume.** How many rows/day across the accounts? If email alone is in the
   thousands, the list needs keyset pagination rather than `OFFSET`, and the
   default view should be "unread/untriaged" rather than "everything".
4. **Should escalations be a filter or a tab?** `escalations` already marks the
   items Leo was pinged about; one chip is cheap, a tab is a commitment.
