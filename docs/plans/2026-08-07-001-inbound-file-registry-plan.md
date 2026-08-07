---
title: "feat: an inbound file registry — every file that arrives, with provenance, viewable and separately rememberable"
status: draft — awaiting decisions
date: 2026-08-07
type: feature
target_repo: ai-prentice-4-all
origin: user request — "I want all uploaded files from chat/telegram/email/whatsapp/calendar saved in Supabase with metadata about where, when and who brought it in"
---

# Inbound file registry

## The problem, precisely

A file that arrives today is read once and forgotten. Two different mechanisms
exist and neither is a record of what arrived:

| | what it holds | lifetime | who can list it |
|---|---|---|---|
| `cache/documents` on the box | every gateway attachment (Telegram, WhatsApp, email, Slack…) | pruned at 24 h | nobody — it is a scratch dir |
| `agent-home-media` Supabase bucket | agent-home chat uploads only | forever | nobody — no UI, no index |
| `rag_documents` / `rag_chunks` | text explicitly ingested via `hermes memory rag ingest-*` | forever | the Documents tab on `/memory` |

So "what did Leo send me last month" is unanswerable, and the Documents tab
answers a *different* question — "what did we deliberately memorise" — which is
why the 9 files in the bucket do not appear in it.

The requested design keeps that distinction and adds the missing layer:

```
arrival  →  FILE REGISTRY (always, automatic, provenance)   → new /files page
                    │
                    └─ explicit ask, or a triage skill's decision
                                    ↓
                        MEMORY / RAG (opt-in, chunked, embedded) → /memory
```

Registration is a fact ("this file arrived"). Ingestion is a judgement ("this
matters"). Conflating them is what makes a corpus full of group-chat memes.

## 1. The registry

### Table `file_assets` (app schema, alongside `memories` / `rag_documents`)

```sql
CREATE TABLE IF NOT EXISTS file_assets (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id  TEXT NOT NULL,          -- C1 principal, from the channel binding
    visibility     TEXT NOT NULL,          -- private:<owner> by default
    -- provenance: where, when, who
    surface        TEXT NOT NULL,          -- agent_home | telegram | whatsapp | email | calendar | …
    account_id     TEXT,                   -- which of my inboxes received it
    conversation   TEXT,                   -- chat / thread / mailbox id
    sender_id      TEXT,                   -- raw channel handle of the sender
    sender_name    TEXT,                   -- display name as the platform gave it
    message_id     TEXT,                   -- back-reference to the message
    received_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- the file itself
    filename       TEXT NOT NULL,
    content_type   TEXT NOT NULL,
    byte_size      BIGINT NOT NULL,
    sha256         TEXT NOT NULL,
    storage_bucket TEXT NOT NULL,
    storage_path   TEXT NOT NULL,          -- <owner>/<surface>/<yyyy-mm>/<sha256>-<name>
    -- the memory link, when one exists
    document_id    UUID REFERENCES rag_documents(id) ON DELETE SET NULL,
    remembered_at  TIMESTAMPTZ,
    remembered_by  TEXT,                   -- 'user' | '<skill name>'
    UNIQUE (owner_user_id, sha256)
);
```

`UNIQUE (owner_user_id, sha256)` makes re-sends free and collapses the three
copies of the DBS grant PDF into one row with the earliest `received_at` — the
same content-hash trick RAG ingestion already uses. RLS follows the existing
`scope_filter` / `apply_scope_rls` helpers, so a member sees their own files and
anything shared, and an owner-role read is elevated and labelled exactly as it
is for memories today.

### Where the bytes go

The `agent-home-media` bucket, one storage layout for every surface. agent-home
already writes there through its BFF; the gateway path needs a small Python
storage client (`httpx` against the Storage REST API) because Python has none
today. That requires `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` in the hermes
`.env` — they currently exist only in `agent-home.env`. Reads are always
short-lived signed URLs minted server-side after an ownership check; the bucket
stays private (the existing `canReadMediaPath` rule generalises to "the object's
`file_assets` row must be readable by you").

### The two hook points

Every surface funnels through one of two places, so this is two call sites, not
eleven:

1. **Gateway** — `MessageEvent` arrives with `media_urls` already cached, and
   `source` carries platform, `account_id`, sender and the resolved
   `internal_user_id` (`gateway/inbound.py:155`). Register there, after caching,
   before the turn. Email and WhatsApp attachments come through the same path;
   calendar events arrive as `InboundEvent`s from `gateway/producers.py` and
   carry no file payload today, so they register nothing until a poller supplies
   attachments — the seam is the same.
2. **agent-home** — `POST /api/chat/upload` already uploads to the bucket and
   knows the principal; it only needs the registry insert
   (`agent-home/src/app/api/chat/upload/route.ts`).

Registration is best-effort and never breaks a turn: a Storage or DB failure
logs and the message proceeds.

### Backfill

One command walks the existing bucket prefixes and inserts rows for the 9
objects already there, taking `received_at` from the object's `created_at` and
`surface = agent_home`. Duplicates collapse on the hash.

## 2. Remembering a file (opt-in, two triggers)

Nothing auto-ingests. Two paths, both landing in the same place:

- **2.1 The user asks** — "remember that grant PDF" in chat or Telegram. Served
  by a `remember-file` skill: it finds the registry row (by filename, or the
  most recent file in this conversation), extracts text, ingests it as a RAG
  document with `source_kind = 'file'` and `source_ref = <file_assets.id>`, then
  stamps `document_id` / `remembered_at` / `remembered_by='user'` on the row.
- **2.2 A triage skill decides** — the email/WhatsApp/calendar triage skills
  call the same skill with `remembered_by='<skill>'`, so the audit line says
  which skill judged it important.

Both are one CLI entry point (`hermes memory rag remember-file <id|path>`), so
the skill is a thin wrapper and the logic is tested once.

**This is where the current text-only limit bites.** Ingestion refuses PDF/DOCX
(`hermes_cli/rag_files.py: CONVERTIBLE_SUFFIXES`), and PDFs are most of what
actually arrives. Remembering is close to useless without extraction, so the
plan adds `pypdf` (+ `python-docx`) behind the existing lazy-dependency pattern:
present → extract; absent → the same explicit "convert it first" skip, never a
silent empty document.

## 3. The `/files` page

A new agent-home route, `SECONDARY_NAV` (`Files`, alongside Activity), reusing
the memory page's shape so it needs no new vocabulary:

- a list of registry rows, newest first, each showing filename, type/size, the
  provenance line — *WhatsApp · from Ada Wong · 6 Aug 14:08* — and whether it is
  remembered;
- filters: surface, sender, date range, remembered / not;
- query: filename and sender substring first (cheap and predictable), and
  full-text over extracted content for files that have been remembered;
- click → a detail panel with a **View / Download** action (a signed URL from
  `GET /api/files/<id>/content`) and, when remembered, a link to that document's
  passages on `/memory`;
- a **Remember this file** action, so 2.1 is available without typing.

BFF routes: `GET /api/files`, `GET /api/files/<id>`, `GET /api/files/<id>/content`,
`POST /api/files/<id>/remember` — all principal-scoped, all read-through-RLS,
matching `src/app/api/memory/*`.

## 4. The dead link on `/memory`

Reproduce first, then fix — but the shape is already clear. A Documents-tab
entry today links to `/memory?document=<id>`, which re-filters the same list; it
never opens the file, because the only locator stored is a box path
(`/opt/data/hermes-home-staging/uploads/rag-smoke-test.md`) and agent-home
deliberately serves no arbitrary path. So the link "works" and is useless — the
reported symptom.

With the registry the honest fix exists: a document ingested from a registered
file carries `source_ref = <file_assets.id>`, so the entry links to
`/files/<id>`, where View/Download is a signed URL. Legacy documents whose
`source_ref` is a bare path keep the passages link and gain an explicit "not
stored — ingested from a path on the box" line instead of a link that pretends.

## Testing

- `file_assets` RLS: owner reads own, member cannot read another member's,
  owner-role elevation is labelled — the pattern in `tests/hermes_cli` for
  memories and grants.
- Registration is idempotent: the same bytes twice yield one row.
- Registration failure does not break the message turn.
- The gateway hook stamps the right surface/sender for a Telegram and an email
  attachment.
- `remember-file`: registers → ingests → `document_id` stamped; a PDF with
  `pypdf` absent skips with the conversion hint rather than an empty document.
- Frontend: list renders provenance, filters narrow, the content route refuses
  another principal's file, the `/memory` document link resolves to `/files/<id>`.
- Live: a real file sent over Telegram and one uploaded in agent-home both
  appear on `/files` with correct provenance, and one of them remembered end to
  end.

## Sequencing

1. `file_assets` + RLS + storage client + backfill (registry exists, nothing uses it)
2. gateway + agent-home hooks (files start accumulating)
3. `/files` page and BFF routes (item 3 — visible value)
4. `remember-file` CLI + skill + PDF/DOCX extraction (item 2)
5. `/memory` document link repointed (item 4)

Each step is a separate PR against `develop`, rebased before push — several
agents are working this repo.

## Decisions needed before step 1

1. **Which schema.** Memory runs in `dev` (`datastore.mode: dev`), but the
   gateway resolves channel senders against `app_prod` (`gateway/run.py:3320`).
   As it stands a Telegram file's owner and your `/memory` page live in
   different schemas and the file would never show up as yours. Options: make
   channel binding follow `datastore.mode` (my preference — one truth per
   deployment), or move the live memory tier to `prod`.
2. **What counts as a file.** Every attachment including images, voice notes and
   stickers from group chats — or documents only, and/or only 1:1 conversations?
   This decides whether the registry is a useful record or a meme archive.
3. **Caps.** Per-file size limit for registration (agent-home's upload cap is
   10 MB today) and any retention at all, given Supabase Storage is billed by
   volume.
4. **PDF/DOCX extraction.** Confirm adding `pypdf` (+ `python-docx`) as lazy
   optional dependencies — without them item 2 covers almost none of your files.
5. **Skill or tool.** `remember-file` as a skill driving the CLI (narrow-waist,
   no new tool in every prompt) — or a first-class `remember_file` agent tool,
   which is more reliable to trigger but costs a tool slot on every API call?
6. **Nav placement.** `Files` in the secondary nav, or promoted into the
   five-item bottom bar on the phone (which would push something out)?
