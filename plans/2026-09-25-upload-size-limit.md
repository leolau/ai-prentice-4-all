# Remove the 100 MB upload cap (chat / files-import / project files)

Date: 2026-09-25
Status: implemented — deployed to box; storage limit raised to 10 GiB

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
- `/api/chat/upload`, `/api/files/import`, `/api/projects/[slug]/files/upload`
  all consult `uploadMaxBytes()`.
- Composer pre-check removed — the server's `detail` message already surfaces
  on a 413.
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
