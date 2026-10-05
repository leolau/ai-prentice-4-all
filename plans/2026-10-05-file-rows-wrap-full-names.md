# Plan: file rows wrap the full name instead of truncating

Status: **shipped** (PR #504, deployed `7b76af935`).

## Problem

In the unattached-files warning (and the All files list), each row's
filename was CSS-`truncate`d to one line — long names like Drive-file-id
artifacts read as `1tlnvV-Tl8Eu_gvfzdj0djv_MN0xjWvx…` with no way to
see the rest (no title tooltip in AttachRow/AllFiles). The single-target
attach button also truncated the output's title to 18 chars
(`shortTitle`): `Attach to "4 files in docxs f…"`.

## Change

- `UnattachedWarning.tsx` `AttachRow`: title span `truncate` →
  `break-all` — the name wraps within the row (flex-wrap already keeps
  the badge and Open button sane) and is fully shown.
- Same in `AllFiles.tsx` `FileRow` title (same bug class — a filename
  you can never read in full).
- Attach button names the single target in full:
  `Attach to “<full output title>”` — it is the only place naming the
  target when no picker renders. `shortTitle` had no other caller, so
  the helper and its test lines are removed.
- Left alone (deliberate middle-ellipsis + hover tooltip from #498):
  `LatestOutputsShelf` tiles, `DeliverableCard` delivery names.

## Tests

- Existing suites cover the rows (AttachRow locking, AllFiles listing);
  `artifacts.test.ts` drops the `shortTitle` assertions.

Verified: outputs + ProjectDetailView suites (95) green, eslint clean.
