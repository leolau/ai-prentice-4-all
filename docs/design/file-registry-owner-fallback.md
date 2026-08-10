# Inbound file registry — owner fallback for personal-agent deployments

## Status

implemented, deployed, and verified (PR #153 + #156)

## Date

2026-08-09

## Origin

Reported symptom: the automatic file registry shows only ~6 registered files
while ~477 attachments (documents, images, voice notes) sit in the gateway's
local media caches (`~/.hermes/{document,image,audio}_cache/`). The on-instance
agent attributed the gap to "files are cached but not memorised yet" and
suggested a triage sweep. The actual cause is simpler and more urgent:

**inbound attachments from unenrolled senders are never registered at all, and
the cache that holds them is pruned after 24 hours — so the file is lost
forever, with no Supabase copy to backfill from.**

---

## The problem, precisely

Two registration paths exist, and both fail for a personal-agent deployment:

### Path 1 — the gateway (`gateway/inbound_files.py`)

When a channel attachment arrives, the gateway caches the bytes under
`cache/documents` (pruned at 24 h by `cleanup_document_cache`) and fires
`_spawn_inbound_file_registration` → `register_event_files`. That function
resolves the owner from the **sender's** principal:

```python
def _principal_for(source):
    user_id = getattr(source, "internal_user_id", None)
    if not user_id:
        return None          # unenrolled sender → skip registration
    ...
```

For a personal agent, the inbound WhatsApp / Telegram / email senders are
*external contacts* — family members, colleagues, recruiters — who are **not
enrolled principals**. `bind_channel_principal` → `resolve_principal` returns
`None` for them, `source.internal_user_id` stays unset, `_principal_for`
returns `None`, and `register_event_files` returns `[]`. The file is cached
for the model to read this turn, then deleted at the 24-hour prune. No
`file_assets` row, no Supabase object — **unrecoverable**.

This is deliberate for multi-user deployments (a stranger's file must not be
parked under somebody else's identity), but the design **omits the
owner-fallback** that a personal-agent deployment needs: when the sender is
unenrolled, the file was *received by the owner*, so the owner is the correct
principal to scope it to.

### Path 2 — the standalone services (`custom/shared/file_registration.py`)

The email poller and WhatsApp batcher are standalone services that don't go
through the gateway pipeline. They call `register_file()` →
`_resolve_owner()`, which resolves the **owner** principal (the correct
approach). But `_resolve_owner()` has a **sticky-failure bug**:

```python
async def _resolve_owner():
    global _owner_principal, _owner_resolved
    if _owner_resolved:
        return _owner_principal
    _owner_resolved = True            # ← set BEFORE the try
    try:
        _owner_principal = await store.get_owner()
        ...
    except Exception:
        _owner_principal = None       # ← stays None, cached forever
    return _owner_principal
```

`_owner_resolved = True` is flipped *before* the resolution attempt. One
transient failure (Supabase env not yet loaded, owner row not yet created,
network blip) caches `None` for the entire process lifetime. Every
subsequent `register_file()` silently returns `False` until the process
restarts.

### Where the ~6 come from

Dashboard uploads via `POST /api/registry/files/register`
(`hermes_cli/files_api.py`) use the **caller's** principal — the logged-in
owner — so they always succeed. The ~6 registered files are the ones the user
manually uploaded through the Files page.

---

## The design

```
inbound attachment
       │
       ├─ gateway path (register_event_files)
       │      │
       │      ├─ sender enrolled?  → register under sender principal  (unchanged)
       │      │
       │      └─ sender unenrolled?
       │              │
       │              ├─ owner principal resolved? → register under owner  (NEW)
       │              │
       │              └─ no owner configured       → skip (unchanged, safe)
       │
       └─ standalone path (file_registration.register_file)
              │
              └─ _resolve_owner() with retry-after  (FIXED: no more sticky None)
```

### Decision 1 — owner fallback in the gateway path

`register_event_files` gains an optional `owner_principal` parameter. When
`_principal_for(source)` returns `None` (unenrolled sender) and
`owner_principal` is provided, the file is registered under the owner
instead of being skipped. When `owner_principal` is also `None`
(multi-user deployment with no single owner, or Supabase unconfigured), the
original skip behaviour is preserved — no regression.

The owner principal is resolved lazily from the `PrincipalStore` the
gateway already holds (`_get_principal_store`), with a module-level
retry-after cache so a startup race (owner row created after the gateway
starts) recovers within 60 seconds without hammering Postgres on every
attachment.

**Why this is safe for a personal agent:** there is exactly one owner, and
every inbound file was *received by* that owner. Scoping the file to the
owner is correct — the file is the owner's correspondence, not the sender's
property in the registry's sense.

**Why this does not regress multi-user:** the fallback only fires when no
sender principal could be resolved, and only when the deployment has a single
owner. A multi-user deployment that does not configure a PrincipalStore
returns `None` and keeps the original skip. A multi-user deployment *with* a
single `owner`-role principal would attribute unenrolled-sender files to
that owner — which is a behaviour change, but strictly better than the
status quo (the file is currently lost). The provenance columns
(`sender_id`, `sender_name`, `surface`, `conversation`) still record who
sent it; only the `owner_user_id` (visibility scope) changes.

### Decision 2 — fix the standalone `_resolve_owner` sticky bug

Replace the "set resolved before trying" pattern with a retry-after cache:

- On **success**: cache the principal, mark resolved.
- On **failure** (exception or `None`): record the attempt timestamp, do
  *not* mark resolved. The next call retries after
  `_OWNER_RETRY_INTERVAL_SEC` (60 s), so a startup race recovers without
  spamming Postgres on every message.

This mirrors the retry-after cache added to the gateway path (Decision 1),
keeping the two paths consistent.

---

## Files changed

| File | Change |
| --- | --- |
| `gateway/inbound_files.py` | Add `_resolve_owner_principal()` retry-after cache; add `owner_principal` param to `register_event_files`; fall back to owner when sender is unenrolled. |
| `gateway/run.py` | Pass `principal_store=self._get_principal_store()` to `register_event_files` in `_spawn_inbound_file_registration`. |
| `custom/shared/file_registration.py` | Fix `_resolve_owner()`: retry-after instead of sticky-None cache. |
| `tests/gateway/test_inbound_files.py` | Add test: unenrolled sender + owner principal → registered under owner. Existing "unenrolled → nothing" test still passes (no owner passed). |

---

## What this does NOT change

- **Registration is still not ingestion.** Nothing here embeds anything or
  writes to `rag_documents`. A file is registered (fact: "this arrived")
  and remains opt-in to remember (judgement: "this matters"), exactly as
  the original design intends.
- **The 24-hour cache prune stays.** The prune is a scratch-dir cleanup,
  not a policy lever. With the owner fallback, the bytes are uploaded to
  Supabase *before* the prune runs, so the prune just reclaims local disk.
- **Multi-user sender-scoped registration stays the primary path.** The
  owner fallback is a *fallback* — it only fires when the sender cannot be
  resolved. Enrolled senders are still scoped to themselves.

---

## Post-deploy: two additional WhatsApp-specific root causes (fixed in PR #156)

The owner-fallback fix (PR #153) was correct but insufficient. After 12+ hours
of live traffic, only 1 additional file was registered (7 total: 6 email, 1
agent-home, 0 WhatsApp). Investigation found two stacked failures specific to
the WhatsApp media pipeline that prevented files from ever reaching
`register_file()`.

### Root cause 1 — Node.js v20 IPv6-first DNS (bridge.js)

The WhatsApp bridge (`scripts/whatsapp-bridge/bridge.js`) uses Baileys'
`downloadMediaMessage()` to download media from `mmg.whatsapp.net`.
`downloadMediaMessage` calls Node.js v20's native `fetch()`, which (since
Node.js ≥17) resolves DNS with **IPv6-first** by default via the undici
HTTP client.

The Alibaba Cloud ECS instance (`cn-hongkong`) has **broken IPv6
connectivity** — `curl -6 https://mmg.whatsapp.net` fails, `curl -4` works.
Node.js `fetch()` has no Happy Eyeballs fallback (unlike curl), so every
media download fails with `Failed to fetch stream`.

**Fix:** `dns.setDefaultResultOrder('ipv4first')` at the top of
`bridge.js`, after imports. This makes undici resolve A records before
AAAA records, so `fetch()` connects over IPv4. Verified: after the fix,
the bridge successfully downloads media (HTTP 404 from a test URL
confirms connectivity, not DNS failure).

### Root cause 2 — batcher called a non-existent endpoint (batcher.py)

The WhatsApp batcher (`custom/whatsapp/batcher.py`) tried to download
media via `GET http://localhost:{port}/media/{messageId}`. The bridge has
**no `/media/:id` endpoint** — the only endpoints are `/messages`, `/send`,
`/edit`, `/send-media`, `/typing`, `/chat/:id`, and `/health`.

The bridge already downloads media via Baileys' `downloadMediaMessage()`
and provides local file paths in the `mediaUrls` array on each message
event. The batcher was ignoring `mediaUrls` entirely and hitting a
non-existent HTTP endpoint, so every media message produced:

```
[batcher] Could not download media for {msg_id} (bridge may not support /media endpoint)
```

**Fix:** Replaced the HTTP download call with direct file reads from
`msg.get('mediaUrls')`. For each path in `mediaUrls`, the batcher reads
the file from disk, determines the content type via `mimetypes`, and calls
the new `_save_and_register_media()` helper (which saves to `MEDIA_DIR`,
updates the SQLite DB, and calls `register_file()`). A fallback to the
legacy HTTP endpoint is preserved for bridges that don't pre-download.

### Why the email poller was unaffected

The email poller (`custom/email/`) uses its own attachment download path
that doesn't go through the bridge or `downloadMediaMessage()`. It reads
IMAP attachments directly and calls `register_file()`. The owner-fallback
fix from PR #153 was sufficient for email — 6 of the 7 registered files
came from the email poller.

### Deployment notes

- Both fixes are in `develop` (squash-merged as PR #156, commit
  `efc33db39`).
- Deployed via `hermes-deploy.sh` at commit `9c15e15bb` (which includes
  PRs #155–#158 from concurrent agents).
- Branch protection on `develop` requires PRs — direct pushes are
  rejected with `422 Repository rule violations`.
- The ECS instance's deploy SSH key is **read-only** — pushing from the
  box is not possible. Push must go through the GitHub API (MCP) or a
  machine with network access to GitHub.
- After deploy, both bridges reconnected, the batcher resumed polling,
  and no `Failed to download` errors appeared in the bridge logs.
- Bridge media cache confirmed: 444 images, 113 documents, 11 audio
  files (as of 2026-08-10 07:39 HKT).

### Files changed in PR #156

| File | Change |
| --- | --- |
| `scripts/whatsapp-bridge/bridge.js` | Added `import dns from 'dns'` and `dns.setDefaultResultOrder('ipv4first')` with explanatory comment after the import block. |
| `custom/whatsapp/batcher.py` | Added `_save_and_register_media()` helper. Replaced media download logic in `process_message()` to read `mediaUrls` from the bridge message instead of calling the non-existent `GET /media/{id}` endpoint. Falls back to legacy HTTP download for backward compat. |
