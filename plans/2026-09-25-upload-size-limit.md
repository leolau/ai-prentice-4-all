# Remove the 100 MB upload cap (chat / files-import / project files)

Date: 2026-09-25
Status: implemented — deployed to box; storage limit raised to 10 GiB

> **Update (same day, later):** this box-side fix was silently lost during
> the Alibaba -> Hetzner migration (`FILE_SIZE_LIMIT` was back to its
> `storage-api` default of 50 MB on the new box, then found at 100 MB after
> an intermediate manual edit — either way, not the 10 GiB set here) because
> it lived only in a hand-edited, untracked `docker-compose.yml` on the box,
> which the migration replaced with a fresh template. Root-caused live via a
> direct, isolated `curl` upload to Kong's storage endpoint (bypassing the
> browser and Next.js entirely) that reproduced `413 Payload too large`
> after ~5 minutes at a throttled rate — the multi-minute delay is just how
> long it takes to fully buffer a large upload before the size check runs,
> which looks exactly like a timeout and cost real debugging time chasing
> unrelated timeout fixes (app-mcp keepalive, Node's `requestTimeout`,
> undici's client timeout — see `2026-09-25-folder-bridge-import-keepalive.md`
> and `2026-09-25-prod-migration-guardrails.md`) before this was found.
> Re-fixed and made durable: `FILE_SIZE_LIMIT=5368709120` (5 GiB) now lives
> in `/opt/data/supabase/docker/.env` (which the migration runbook explicitly
> preserves) rather than only in `docker-compose.yml`, and the compose file
> reads it as `${FILE_SIZE_LIMIT:-104857600}` so a future template refresh
> still uses the `.env` value if `.env` survives, and falls back to a sane
> documented default (not silently to a much smaller one) if it doesn't.
> Verified with a real 200 MB upload directly to Kong (previously rejected at
> the 100 MB ceiling; now succeeds in ~2s).

## Problem

Uploading a WhatsApp chat export (>100 MB zip) was refused client-side:
"exceeds the 100 MB limit." Three stacked limits existed:

1. `UPLOAD_MAX_BYTES` (100 MB) — `agent-home/src/lib/chat/upload-limit.ts`,
   enforced in `/api/chat/upload`, `/api/files/import`, and pre-checked in the
   Composer before upload.
2. A separate 10 MB cap in `/api/projects/[slug]/files/upload`.
3. Supabase Storage `FILE_SIZE_LIMIT=52428800` (50 MB) — the real backend
   ceiling, which made the app's 100 MB cap unreachable anyway.

## Changes

- `upload-limit.ts` → `uploadMaxBytes()`: unlimited by default;
  `AGENT_HOME_UPLOAD_MAX_BYTES` reinstates a cap for deploys that need one.
  `uploadTooLargeDetail()` renders the refusal copy dynamically.
  `UPLOAD_WARN_BYTES` (100 MB) is the client-side advisory threshold.
- `/api/chat/upload`, `/api/files/import`, `/api/projects/[slug]/files/upload`
  all consult `uploadMaxBytes()`.
- Composer shows an "Upload anyway / Cancel" confirmation for files over the
  advisory threshold instead of refusing — the user overrides, the server
  still enforces whatever real cap the backend has.
- Box: `FILE_SIZE_LIMIT` raised to 10 GiB in
  `/opt/data/supabase/docker/docker-compose.yml` and the `supabase-storage`
  container recreated (compose file backed up alongside). Bucket
  `agent-home-media` has no per-bucket override, so the global applies.
  Caddy has no request-body cap.

## Tests

- `chat/upload` test: default-unlimited case (101 MB accepted) + env-stubbed
  413 case.
- `files/import` test mock updated to the function exports.
- 15 tests pass; tsc + eslint clean.
