# Output deliveries: view & download files for review

Date: 2026-09-25
Status: implemented — 5 new vitest cases pass, 164 project tests pass, tsc + eslint clean

## Problem

The project Outputs panel lists each delivery as plain text
(`delivered on a run → label · date`). When a delivery carries a file
(`link_kind="file"` + `link_ref` = principal-scoped storage path), there is
no way to view or download the artefact, so the human cannot review it before
pressing **Accept** — and accepting is the human-only judgement step (§6.1).

Reported by user: "There is supposed to be a output file that I need to
review, but this UI does not allow me to view nor download any file."

## Approach

Reuse the file-resolution pattern already shipped in `FilesPanel`:

1. Click the delivery's file name → `GET /api/files/by-path?path=<link_ref>`
   resolves the storage path to the newest visible `file_assets` registry row
   → open the shared `FileDetail` dialog (provenance + View + Download via
   `/api/files/{id}/content`, which re-gates on registry visibility).
2. If the path has no registry row (deliveries registered best-effort), fall
   back to a dialog with View / Download through
   `/api/chat/media/content?path=…` (`mediaContentRef`), which still enforces
   the C2 principal-prefix ownership check server-side.
3. `link_kind="url"` deliveries render as an external link.
4. `attachment`, `session`, `memory` and ref-less deliveries keep their
   current text rendering (no resolvable surface exists yet).

## Changes

- `agent-home/src/components/files/FileRefOpener.tsx` (new) — shared hook +
  fallback dialog so FilesPanel and OutputsPanel resolve file refs the same
  way. FilesPanel refactored onto it (removes its inline duplicate).
- `agent-home/src/components/projects/panels/OutputsPanel.tsx` — delivery
  rows gain a clickable file name (opens the resolver) and the existing
  metadata line is preserved.
- Tests: `OutputsPanel.test.tsx` covering file-delivery view path, url
  delivery, and plain-text kinds.

## Verification

- `vitest` for the new/changed components + FilesPanel regression.
- `tsc --noEmit`, `eslint` on touched files.
