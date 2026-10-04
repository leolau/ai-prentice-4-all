# Outputs — remote-copy fallback for rotted workspace refs + distinguishable titles

## Problem

Scratch card workspaces are wiped on task completion (by design —
`kanban_db._cleanup_workspace`). A delivery or draft recorded with
`link_kind="workspace"` pointing into that workspace then has a dead
`link_ref`: the artifact read resolves `serve=None`, `href=None`, and the UI
renders a dead disabled **Open** ("This file isn't reachable from here").

Hit on the MOU project: four `定稿v2.docx` deliveries were attached fine, then
both card workspaces were cleaned. The real durable copies were already
uploaded to Google Drive by the filing card (`meta.new_files` recorded
name/link/sha256 per upload) — but the artifacts read never looked at run
metadata for remote copies.

Secondary wart: the Latest-outputs shelf truncated titles at the end, so the
four `自學中文書寫功夫…_<party>_定稿v2.docx` tiles all rendered
identically.

## Fix

`hermes_cli/projects_outputs_api.py`:

- `_uploaded_copies(ctx)` — walks each run's metadata for document-URL
  `link`/`url`/`webViewLink` values labelled by a sibling `name`/`filename`/
  `title` (the `new_files` upload convention), building `basename → URL`.
- `build_artifacts` — a `workspace`/`file` delivery whose local ref isn't
  servable falls back to the recorded remote copy (`href` = Drive URL,
  `location` = host). Unattached drafts whose file is gone get the same
  fallback.

`agent-home` (`artifacts.ts`, `LatestOutputsShelf.tsx`,
`DeliverableCard.tsx`):

- `middleEllipsize()` keeps the distinguishing tail (party name + version +
  extension) instead of plain end-truncation; full title on hover via
  `title=` attribute.

## Data repair (done on the box, 2026-10-05)

The four `定稿v2` delivery rows on
`prepare-mou-for-self-learning-chinese-writing-kungfu-ar-card` were repointed
from the deleted workspace paths to the verified Drive copies
(`link_kind='url'`, `link_ref=<docs.google.com link>`). Open works
immediately without waiting for the deploy.

## Verification

- `test_projects_outputs_api.py` — two new regressions (rotted workspace
  delivery → remote href; rotted draft → remote href). 16/16 pass.
- vitest outputs suite: 40 pass; tsc + eslint clean.

## Status

- [x] Data repair (4 deliveries now open their Drive copies)
- [x] Implemented + regression tests
- [ ] Merged / deployed
