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
and default — tab on `/inbox` with search, filters, user tags, keyset
pagination, and a linkable detail route. Two foreign keys give the requested
links: `file_assets.inbound_item_id` (item ⇄ its attachments) and
`inbound_items.document_id` (item → what was remembered from it).

**In scope:** WhatsApp, email, calendar, plus every gateway channel that
already flows through `gateway/inbound.py` — the schema is surface-agnostic.
Search (filters + Postgres FTS, CJK-segmented, + a substring fallback),
**tags reusing the existing session-tag system**, **keyset pagination**, an item
detail view, both link directions, a backfill of existing history, and a
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

**Which store, and why this is in Postgres at all.** The rule the codebase
already follows: **SQLite is one box's working state; Supabase Postgres is
shared user data.** SQLite holds session history (`hermes_state.py`), the
Kanban board, cron state, the agent's todo scratchpad, and the triage
pipeline's raw store, batching and digests — machine-local, single-user, no
`visibility` column, safe to rebuild. Postgres holds `memories`,
`rag_documents`, `file_assets`, `tasks`, `goals`, `notifications`, `changes` —
everything carrying `owner_user_id` + `visibility`, scoped by RLS, readable by
`agent-home`. The test: *if a browser or a second person should ever see it, it
is Postgres.* The entire custom pipeline currently sits on the SQLite side of
that line, which is exactly why none of it reaches the Inbox — and why this
plan mirrors into Postgres rather than putting an API in front of
`whatsapp_data.db`.

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
| P1 | CJK search (worked example + fix below) | Postgres's default parser has no Chinese segmentation, so FTS silently returns nothing for words that are plainly present. The fix changes the table, so it cannot be deferred past step 1. | **Recommended: application-side bigram segmentation into a `search_text` column** — no extension, no dependency, works on managed Supabase today. Needs sign-off because it adds a column and makes `search_tsv` derived rather than direct. |
| P2 | one owner or per-member? | `custom/shared/file_registration.py` resolves *the* owner principal. If a member is ever to have their own mailbox, `account_id → principal` needs a mapping table, which changes the registration signature. | Keep the owner binding; add a `TODO` at the seam. Revisit when a second member gets a channel. |
| P3 | ~~volume~~ **decided** | — | **Keyset pagination from the start** (user requirement, 2026-08-10). No `OFFSET` path is built, so the volume count is no longer a blocker; still worth running once to choose the default view. |
| P4 | escalations: chip or tab? | Affects step 4's UI only, not the schema. | A chip. Defer. |

### P1, worked: why Chinese search under-matches

Postgres's default text-search parser splits on whitespace and punctuation. It
has no Chinese dictionary and no segmentation, so an unspaced CJK run is one
token:

```
body:  請問明天的會議改到下午三點嗎
to_tsvector('simple', body)      →  '請問明天的會議改到下午三點嗎':1     -- ONE lexeme
websearch_to_tsquery('simple','會議') → '會議'
match?                            →  NO
```

The user searches 會議 ("meeting"), the word is in the message, and the result
list is empty. English escapes this only because spaces do the segmenting.

### P1, recommended fix: segment at write time, in application code

The two textbook fixes are Postgres extensions — `zhparser`/SCWS (dictionary
segmentation) and `pg_bigm` (CJK-aware 2-grams). Both need superuser
`CREATE EXTENSION` on the database server, **and neither is in Supabase's
supported-extension list**, so on managed Supabase they are not available at
all. Waiting for them means waiting for a self-hosted Postgres.

**Recommendation: bigram the CJK runs ourselves before indexing.** No
extension, no new dependency, no superuser, works on stock Postgres today:

```python
def searchable(text: str) -> str:
    """Latin words unchanged; CJK runs expanded to overlapping bigrams."""
    out = []
    for run in _split_cjk_and_other(text):
        if _is_cjk(run):
            out.extend(run[i:i+2] for i in range(len(run) - 1)) or out.append(run)
        else:
            out.append(run)
    return " ".join(out)
```

```
'請問明天的會議改到下午三點嗎'
  → '請問 問明 明天 天的 的會 會議 議改 改到 到下 下午 午三 三點 點嗎'
query '會議' → searchable('會議') = '會議'   →  MATCH, on the GIN index
```

This is precisely what `pg_bigm` does, moved into our own code. Consequences:

- **Schema:** `search_tsv` can no longer be `GENERATED` (a generated column may
  only call immutable SQL). Add a plain `search_text TEXT` column written by
  the registry, and keep `search_tsv tsvector GENERATED ALWAYS AS
  (to_tsvector('simple', search_text)) STORED`. One extra column, same index.
  **This must land in step 1** — retrofitting it later rewrites the table.
- **Query side** runs the identical `searchable()` on the user's input, so
  segmentation is symmetrical by construction. Put the function in one module
  and let both sides import it; a divergence between write-time and query-time
  tokenisation is the classic way this breaks.
- **Single characters** (a 1-char query, or a 1-char run) have no bigram; keep
  the `ILIKE` fallback for those. That is the fallback's real job — not
  covering all of Chinese, just the sub-bigram edge and phone fragments.
- **Cost:** roughly one lexeme per character, so the CJK index is ~2× an
  English one of the same length. At inbox volumes that is irrelevant.
- **False positives** across word boundaries are possible (searching 天的
  matches). Acceptable: an inbox tolerates an extra result, never a missing
  one, and `ts_rank_cd` pushes accidental matches down.

Better precision later, if it is ever wanted: `jieba` for real word
segmentation, writing into the *same* `search_text` column — a reindex, not a
schema change. Ship bigrams first; they are dictionary-free and never miss.

Also enable **`pg_trgm`** (this one *is* available on Supabase) with a GIN
index on `search_text`, so the `ILIKE` fallback stops being a sequential scan.

### Two requirements added after the spec merged (2026-08-10)

**Tags.** The list must support tags alongside the surface/sender/date filters,
and must **reuse the tagging system that already ships** for chat sessions
rather than adding a second one — which means promoting that system's
vocabulary from session SQLite to Postgres and generalising it to any entity.
See §1a; this lands in step 1, not step 4.

**Keyset pagination, not `OFFSET`.** `OFFSET 5000` makes Postgres fetch and
discard 5000 rows to return 50, so page 100 costs 100× page 1, and an arrival
mid-scroll shifts every later page by one (you see a duplicate row). Keyset
instead remembers the last row and asks for what follows it:

```sql
WHERE (occurred_at, id) < ($cursor_ts, $cursor_id)
ORDER BY occurred_at DESC, id DESC
LIMIT $n
```

Index-only against `(owner_user_id, occurred_at DESC, id DESC)`, constant cost
per page, stable under concurrent inserts. The tradeoff — next/previous rather
than jump-to-page-7 — is the right one for an inbox. The API therefore returns
an opaque `next_cursor` (base64 of `occurred_at|id`) rather than an `offset`,
and the ranked-search path uses `(rank, occurred_at, id)` as its cursor tuple
so ordering stays total.

### §1a. Tags — reuse the existing system, do not invent a second one

**A tagging system already ships.** Session tagging (schema v18) is complete
and in production use:

| layer | where |
|---|---|
| store | `hermes_state.py` — `session_tags` (`id`, `name` UNIQUE, `color`, `created_at`) + `session_tag_map` (`session_id`, `tag_id`, `assigned_at`, `source`) |
| logic | `list_tags`, `create_tag`, `get_session_tags`, `add_tag_to_session`, `remove_tag_from_session`, `delete_tag`, `filter_session_ids_by_tags(include, exclude, match="any"\|"all")` |
| API | `GET/POST /api/sessions/tags`, `DELETE /api/sessions/tags/{tag_id}`, `GET/POST /api/sessions/{id}/tags`, `DELETE /api/sessions/{id}/tags/{tag_id}`, `POST /api/sessions/{id}/tags/suggest` (LLM suggestions) |
| client | `listTags`, `createTag`, `getSessionTags`, `addSessionTag`, `removeSessionTag`, `deleteTag`, `suggestSessionTags` in `agent-home/src/lib/api/client.ts` |
| types | `SessionTag`, `TagSuggestion` in `agent-home/src/types/index.ts` |
| UI | `components/chat/TagFilterBar.tsx` (tri-state chips: tap = include, again = exclude, again = clear; AND/OR toggle), tag editing in `SessionModal.tsx`, vocabulary management in `settings/SettingsView.tsx` → `TagsSection` |

It is better than the child table this plan originally proposed: a tag is a
*named, coloured, first-class row* rather than a free string, `source` on the
map already carries provenance (`manual` vs an LLM suggestion), and
`filter_session_ids_by_tags` already implements include/exclude with AND/OR.
Incomings must reuse all of it. **No `inbound_item_tags` table, no second tag
vocabulary, no second chip component.**

**The one real obstacle:** the tag vocabulary lives in the session SQLite DB,
which is machine-local and has no `owner_user_id`/`visibility`. `inbound_items`
is in Postgres under RLS. Tagging a Postgres row from a SQLite tag table across
two stores is not possible without exactly the dual-write this repo forbids.

**Therefore, step 1 promotes the tag vocabulary to Postgres and generalises the
assignment to any entity** — which is also what "re-use it in the entire
system" requires, since files, tasks and memories all want the same chips:

```sql
CREATE TABLE IF NOT EXISTS tags (            -- the vocabulary, C2-scoped
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    visibility    TEXT NOT NULL,
    name          TEXT NOT NULL,
    color         TEXT NOT NULL DEFAULT 'blue',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_user_id, lower(name))
);

CREATE TABLE IF NOT EXISTS tag_assignments (  -- polymorphic, one row per tagging
    tag_id      UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    entity_kind TEXT NOT NULL,                -- 'session' | 'inbound' | 'file' | 'task'
    entity_id   TEXT NOT NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source      TEXT NOT NULL DEFAULT 'manual',   -- 'manual' | 'llm' | '<skill>'
    PRIMARY KEY (tag_id, entity_kind, entity_id)
);
CREATE INDEX ON tag_assignments (entity_kind, entity_id);
```

`entity_id` is `TEXT`, not `UUID`, because session ids are not UUIDs; the
tradeoff is no FK, so deletion cleanup is the owning registry's job (one
`DELETE FROM tag_assignments WHERE entity_kind=$1 AND entity_id=$2` in its
delete path). RLS on `tags` via the standard `apply_scope_rls`; assignments are
reached only through a scoped join to their tag, the FG-04 goal-metrics
pattern.

Endpoints generalise to `/api/tags` (vocabulary CRUD) and
`/api/tags/{kind}/{id}` (assign/unassign), with `/api/sessions/tags*` kept as
thin aliases so nothing in the chat UI breaks. `filter_session_ids_by_tags`
becomes `filter_ids_by_tags(entity_kind, include, exclude, match)` with the
same semantics. `TagFilterBar` is lifted from `components/chat/` to
`components/tags/` unchanged — it already takes `SessionTag[]` and callbacks
and knows nothing about sessions; only the type is renamed to `Tag`
(`SessionTag` kept as an alias).

Migration is a `hermes doctor --fix` step: copy `session_tags` → `tags` (owned
by the resolved principal), `session_tag_map` → `tag_assignments` with
`entity_kind='session'`, preserving `source`. Runtime then reads Postgres only
— no read-through fallback to the SQLite tables, per the store-migration rule.

**This makes the tag work bigger than a filter chip** (it now touches sessions,
settings and doctor) and it is a prerequisite for step 3 rather than a step-4
UI detail. Recorded here as a scope change from the merged spec.

P1 and P3 are the ones that cost rework; P2 and P4 do not block.

## Step 1 — the table and the registry

**PR:** `feat(incomings): inbound_items registry with C2 scoping`
**Files:** new `hermes_cli/inbound_registry.py`; new `hermes_cli/tags.py`
(the promoted vocabulary, §1a) + its doctor migration; new
`hermes_cli/text_search.py` (the shared `searchable()` used by both the writer
and the query builder, P1); new `tests/hermes_cli/test_inbound_registry_e2e.py`
and `test_tags_e2e.py`; add the new modules to the core-boundary list in
`agent/core_boundary.py`.

Step 1 is therefore larger than the merged spec implied: it now carries the
tag promotion and the search-text column, both of which are schema decisions
that cannot be retrofitted cheaply.

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
    async def list(principal, *, query="", surfaces=(), kinds=(), tags=(),
                   contact=None, since=None, until=None, importance=None,
                   remembered=None, has_attachments=None, limit=50,
                   cursor=None) -> tuple[list[InboundItem], str | None]
    async def facets(principal) -> dict           # surface/kind/sender/tag counts
    async def add_tag(principal, item_id, tag, *, by="user") -> None
    async def remove_tag(principal, item_id, tag) -> None
    async def mark_remembered(principal, item_id, *, document_id, by) -> InboundItem
    async def attachments(principal, item_id) -> list[FileAsset]
```

`list()` returns the next cursor, not a total: a `COUNT(*)` over a filtered,
RLS-scoped table is the same full scan keyset pagination exists to avoid.
Where the UI wants a number it uses the `/facets` counts, which are grouped
and cheap.

The DDL is in the spec (§1), plus `inbound_item_tags` from §1a above and the
`(owner_user_id, occurred_at DESC, id DESC)` index the cursor needs. Three
points the implementation must not soften:

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
FTS matches a body word and a filter narrows; **a two-character Chinese word is
found by FTS via the bigram path, and a single character falls through to the
substring path** (both asserted, since the write-side and query-side
segmentation drifting apart is the failure mode); tags are
reused from the existing vocabulary rather than duplicated, a member cannot tag
another member's item, an existing session tag still resolves after the
migration, and deleting an item removes its assignments; keyset paging over 200 synthetic rows
returns every row exactly once and inserting a new arrival mid-walk does not
duplicate or skip one.

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
| `GET ""` | `{items, next_cursor}` — params `q`, `surface` (csv), `kind`, `tag` (csv), `contact`, `from`, `to`, `importance`, `remembered`, `has_attachments`, `limit`, `cursor` |
| `GET "/facets"` | `{surfaces, kinds, senders, tags}` with counts, so a chip is never offered for a value with no rows |
| `POST "/api/tags/inbound/{item_id}"` · `DELETE …/{tag_id}` | assign/unassign, via the shared tag router (§1a) — **not** an incomings-specific endpoint |
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
- **Controls:** debounced search box, surface chips and tag chips from
  `/facets`, date range. **Filter state lives in the URL**
  (`/inbox?tab=incomings&q=invoice&surface=email&tag=finance`) so a search is
  linkable and survives reload. The cursor is *not* in the URL — a shared link
  should open the current top of that filtered list, not a stale page.
- **Paging:** infinite scroll via an `IntersectionObserver` that requests the
  next cursor, with an explicit "Load more" fallback button (an observer that
  never fires must not be the only way to reach page 2).
- **Tagging:** reuse `TagFilterBar` (lifted to `components/tags/`) for the chip
  row, and the same tag editor `SessionModal` uses for the detail view — same
  colours, same tri-state include/exclude, same AND/OR toggle, same vocabulary
  the user manages in Settings. If a control here looks or behaves differently
  from tagging a session, it is wrong.
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
   (exercises the FTS path and the fallback), then a Chinese word from a
   WhatsApp message (documents the P1 limitation against real data).
6. Tag two items, filter by that tag, remove one tag; the chip count follows.
7. Scroll past the first page and confirm no duplicate or skipped row while
   messages are still arriving.
8. Remember the email; it becomes findable on `/memory` and the item shows the
   link.
9. Log in as a synthetic member; none of the owner's items — or tags — are
   visible.

Step 9 is the one that must not be skipped. It is the same isolation check the
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
