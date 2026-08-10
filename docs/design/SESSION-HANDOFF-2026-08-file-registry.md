# Session hand-off — prod migration + the inbound file registry (2026-08-07)

Written at a deliberate pause, mid-feature. Everything below is either **done
and live**, or **written and green locally but not deployed**, or **not started
with the exact next steps spelled out**. The plan this implements is
`docs/plans/2026-08-07-001-inbound-file-registry-plan.md`; read it first for the
*why*, then this for the *where we got to*.

The governing requirement, in the user's words:

> I want all uploaded files from chat/telegram/email/whatsapp/calendar all saved
> in the supabase with meta data about where, when and who brought in this file
> … ingested into memory only by (2.1) the user explicitly asking, or (2.2) a
> skill triggered by the email/whatsapp/calendar trigate deciding it matters.

and, clarifying duplicates:

> count every attachment including images/voice notes/stickers as a separate file

That second sentence is the one that bit us; see §4.

---

## 1. Step 0 — the live box moved to `app_prod` (DONE, live)

The registry could not work while the deployment straddled two schemas.
`resolve_mode()` forces any *channel* session to `prod`
(`hermes_cli/datastore.py`), but the box ran `datastore.mode: dev`. So a Telegram
file's owner lived in `app_prod` while `/memory` read `app_dev` — the file would
have registered into a schema the page never looks at.

Two options were on the table: relax the forced-prod rule, or move the box. We
moved the box, because the rule is a deliberate guard and "live traffic in a
schema called dev" is the actual defect.

What was done on `hermes-systest` (`47.83.199.25`, checkout `/opt/data/hermes-agent`):

| Step | Detail |
| --- | --- |
| Backup | schema `backup_20260807_085028`, plus dated copies of `config.yaml` and `agent-home.env`; `app_dev` left untouched |
| Widened prod vectors | `app_prod.memories.embedding` was `vector(256)`; `hermes memory vectors reembed --yes` moved it to 1024 for `BAAI/bge-m3` |
| Created prod RAG | `app_prod` had no `rag_documents`/`rag_chunks` at all — re-ingested the smoke-test source rather than copying rows |
| Copied memories | 109 rows, vectors verbatim (same model, same dimension) |
| Flipped config | `datastore.mode: prod` in `/opt/data/hermes-home-staging/config.yaml`, `AGENT_HOME_DATASTORE_MODE=prod` in `agent-home.env` |
| Restarted | `hermes-gateway.service`, `agent-home.service` |

Verified after: 109 memories all in the configured model's space, 1 document /
3 chunks, semantic search answering in prod, channels and the memory page now
reading the same schema.

**Nothing further is needed here.** `app_dev` remains as the sandbox.

---

## 2. What is written and green (NOT deployed, NOT merged)

Branch: `devin/1786080000-file-registry`, cut from `develop` after #139 merged.

### Python

| File | What it is |
| --- | --- |
| `hermes_cli/file_registry.py` | The `file_assets` table, its RLS, and `FileRegistry` (`initialize`/`register`/`get`/`list`/`mark_remembered`), plus `store_and_register()` and `storage_key()` |
| `hermes_cli/filestore.py` | Minimal server-side Supabase Storage client over `httpx` (`upload`/`signed_url`/`download`/`remove`). Python had none; agent-home's TS client cannot be called from the gateway |
| `hermes_cli/files_api.py` | The read/serve API: list, surfaces+counts, get, signed link, and `POST /register` for agent-home |
| `gateway/inbound_files.py` | Turns a `MessageEvent`'s cached attachments into registry rows with surface/sender/conversation/message provenance |
| `gateway/run.py` | Hook: `self._spawn_inbound_file_registration(event, source)` immediately after `_enrich_channel_source_identity`, fire-and-forget so an upload never delays a reply |
| `hermes_cli/web_server.py` | Mounts the router |

**Route prefix is `/api/registry/files`, not `/api/files`.** The dashboard already
owns `/api/files` (its filesystem browser); mounting there silently shadows it,
because `include_router` runs before the `@app.get` decorators further down the
module. This is a real trap — it was caught only by listing `app.routes`.

### agent-home

| File | What it is |
| --- | --- |
| `src/app/files/page.tsx` | The `/files` page (RSC first paint: principal + first page + surface counts) |
| `src/components/files/FilesView.tsx` | List, search, surface chips, remembered filter, paging, and the `FileDetail` panel |
| `src/app/api/files/route.ts`, `…/surfaces/route.ts`, `…/[id]/route.ts`, `…/[id]/content/route.ts` | BFF forwarding to the Python layer under the bridged principal |
| `src/app/api/chat/upload/route.ts` | Now also registers the upload (best-effort; the bytes are already safe) |
| `src/lib/api/client.ts`, `src/types/index.ts` | `files`/`fileSurfaces`/`file`/`fileLink`/`registerFile` + the `FileAsset` types |
| `src/components/nav-items.ts` | `Files` added to the secondary nav |

Access rule, enforced in both layers: the browser never sees a bucket URL, an
object key or a path on the box. `View`/`Download` hit
`/api/files/:id/content`, which resolves the principal, asks the Python layer
for a link (which re-checks visibility) and 307s to a 5-minute signed URL.
Absent and invisible both return 404 — a distinguishable 403 would confirm
someone else's file exists.

### Tests, all passing locally

```
tests/hermes_cli/test_file_registry_e2e.py   8 passed   (throwaway Postgres via Docker)
tests/gateway/test_inbound_files.py          7 passed
agent-home: vitest                         216 passed   (34 files, incl. 10 new)
agent-home: eslint / tsc --noEmit / build    clean
```

---

## 3. What is NOT done

In dependency order. Items 1–3 are the ones that make the feature actually
useful to the user; 4–5 are polish and cleanup.

### 3.1 Remembering a file (plan §2) — not started

The whole opt-in half. Needed:

- `hermes memory rag remember-file <file-asset-id>` — load the row via
  `FileRegistry.get`, download bytes with `SupabaseStorage.download`, extract
  text, ingest as a RAG document with `source_kind='file'` and
  `source_ref=<file_assets.id>`, then call the already-written
  `FileRegistry.mark_remembered(document_id=…, remembered_by=…)`.
- PDF/DOCX extraction behind the existing lazy-dependency pattern (`pypdf`,
  `python-docx`). Without it this covers almost none of the real uploads —
  `hermes_cli/rag_files.py: CONVERTIBLE_SUFFIXES` is text-only today.
- A `remember-file` skill wrapping the CLI, so the user's "remember that grant
  PDF" works in chat/Telegram (path 2.1), and the email/WhatsApp/calendar triage
  skills can call it with `remembered_by='<skill>'` (path 2.2).
- Do **not** add a core model tool for this — footprint ladder rung 2, as agreed
  in the plan.

The database side is ready: `mark_remembered` exists, is owner-restricted, and
is covered by the E2E test.

### 3.2 Repointing the `/memory` document links — started, not finished

This is the user's item (4) and the last thing I was editing when we paused.
Nothing is half-written in the tree — the intended change is:

1. `hermes_cli/memory_explorer.py`, `get_documents()` (~line 1048): left-join
   `file_assets` on `document_id` and return a `file_asset_id` per document.
   Guard with `to_regclass` so a box without the table still returns documents.
   Do the same for the chunk rows in `_rag_chunk_rows` (~line 569) so the map
   popup can link too.
2. `agent-home/src/types/index.ts`: add `file_asset_id?: string | null` to
   `MemoryDocument` and the chunk row shape.
3. `agent-home/src/components/memory/citation.ts`, `sourceLink()`: when
   `file_asset_id` is present, return `/api/files/<id>/content` ("Open the
   file") *before* falling back to today's `/memory?document=…` filter. Keep the
   external-URL and chat-session branches as they are. Legacy local-path-only
   documents (the smoke-test doc is one) have no registered file and must keep
   the current inward link — never expose `/opt/data/...`.

Why it is dead today: a RAG document's only locator is `source_ref`, a path on
the box that agent-home deliberately will not serve, so the link just re-filters
the same list.

### 3.3 Backfill the 9 objects already in the bucket — not started

`leo_owner/<session>/<uuid>-<name>` objects predate the registry. Write
`hermes files backfill` (or a script under `scripts/`) that lists the bucket
prefix, and for each object inserts a row with `surface='agent_home'`,
`received_at` = the object's `created_at`, and the existing `storage_path`
unchanged (do not re-key old objects).

**Make it idempotent on `storage_path`, not on the hash** — see §4. Re-running
must not invent arrivals that never happened.

### 3.4 Email / WhatsApp / calendar attachment paths — partially verified

The gateway hook covers everything that arrives as a `MessageEvent` with
`media_urls`, which is Telegram and WhatsApp for certain.

- **WhatsApp** (verified 2026-08-10): Two stacked bugs were found and fixed
  in PR #156. The WhatsApp **batcher** (`custom/whatsapp/batcher.py`) was
  calling a non-existent `GET /media/{id}` endpoint on the bridge instead of
  reading the `mediaUrls` file paths the bridge already provides. Separately,
  the WhatsApp **bridge** (`scripts/whatsapp-bridge/bridge.js`) failed to
  download media because Node.js v20's `fetch()` prefers IPv6, which is broken
  on the ECS instance. Fixed with `dns.setDefaultResultOrder('ipv4first')` and
  by reading `mediaUrls` directly. See
  `docs/design/file-registry-owner-fallback.md` §"Post-deploy" for full
  details. The standalone batcher path now calls `register_file()` directly.
- **Email** (verified 2026-08-09): The email poller reads IMAP attachments
  directly and calls `register_file()` in `custom/shared/file_registration.py`.
  6 of the 7 pre-fix registered files came from this path. The owner-fallback
  fix (PR #153) resolved the `_resolve_owner()` sticky-None bug that was
  blocking it.
- **Calendar**: events arrive as `InboundEvent`s from `gateway/producers.py` and
  carry no file payload today. Nothing registers until a poller supplies
  attachments; the seam is the same, but the calendar/event IDs must be carried
  into `account_id`/`conversation`/`message_id` rather than flattened into chat
  fields.

### 3.5 Deploy + live verification — done

Deployed at commit `9c15e15bb` via `hermes-deploy.sh` on 2026-08-10. All 13
services active. The WhatsApp-specific fixes (PR #156) were deployed in the
same run. `SUPABASE_URL` and the service-role key are confirmed readable by
the gateway process — the email poller successfully registered 6 files after
the owner-fallback fix.

End-to-end WhatsApp media registration is pending new media messages arriving
after the fix (the bridge cache has 444+ images, 113 documents, 11 audio files
from pre-fix traffic, but those were never registered and the 24-hour prune
may have already cleaned the local copies). New WhatsApp media messages will
flow through the full pipeline: bridge download → batcher reads `mediaUrls` →
`register_file()` → Supabase Storage + `file_assets` row.

---

## 4. The one design error to not repeat

The first implementation gave `file_assets` a `UNIQUE (owner_user_id, sha256)`
and an `ON CONFLICT … DO UPDATE`, so a re-send returned the first row. The plan
said the same. It is wrong: the user asked for **every attachment counted
separately**, and a hash-collapse deletes precisely the provenance the table
exists to record — three people forwarding the same contract is three facts
about three people.

Corrected, in code and in the plan:

- No unique constraint. `register()` always inserts. Three sends → three rows,
  each keeping its own sender, conversation, timestamp and filename.
- Dedup moved into the bucket: `storage_key()` is now a pure function of owner +
  digest (`<owner>/files/<hh>/<digest16>-<name>`), so N identical arrivals cost
  one object. Surface and timestamp are accepted but unused in the key —
  including them would have re-split the object per channel.
- The E2E test now asserts the opposite of what it used to:
  `test_the_same_file_sent_three_times_is_three_arrivals` checks three ids,
  three senders, one `sha256`, one `storage_path`.

Anyone tempted to "fix the duplicates" on `/files` should re-read this section
first. Duplicate-looking rows are the feature. Group them in the UI if it gets
noisy — do not collapse them in the table.

---

## 5. Other things worth knowing

- **Two storage layouts coexist.** agent-home writes
  `<user>/<session>/<uuid>-<name>` (`scopedMediaPath`); the Python path writes
  the content-addressed key. Both are just strings in `storage_path`, and
  signing works on either, so this is fine — but a backfill or a "find the
  siblings of these bytes" query must not assume one layout.
- **Registration is best-effort everywhere**, by design: a file arriving must
  never fail the conversation it arrived in. The cost is that failures are
  invisible apart from a log line. If the registry ever needs to be
  *authoritative*, add a retry queue — do not make the hook blocking.
- **`default_registry()` imports `_role_reads_configured` from the memory
  store.** Private helper, deliberately reused so files and memories agree about
  downward role reads. If it moves, this breaks; promoting it to a public helper
  would be an improvement.
- **agent-home's `withPrincipalContext` is `BEGIN READ ONLY`**, which is why
  registration goes through the Python API rather than a direct insert from the
  Next.js layer.
- Multiple agents work this repo — re-sync with `origin/develop` before picking
  this up.
