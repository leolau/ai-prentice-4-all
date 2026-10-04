# Artifacts read — dedupe files/URLs claimed by multiple cards

## Problem

The Outputs "produced but not linked" warning listed identical rows: the same
physical file appeared once per card that named it. On the MOU project
(`prepare-mou-for-self-learning-chinese-writing-kungfu-ar-card`), a follow-on
card that filed another card's workspace files claimed the same four paths in
`meta.artifacts`, producing 31 unattached drafts for 25 real files.

Worse than cosmetic: artifact ids are `f:{card}:{hash(path)}`, but
`path_to_id` maps each path to one id — attaching via the UI marked only one
row attached and left the identical sibling unattached forever.

## Fix

`hermes_cli/projects_outputs_api.py`:

- `_files_by_id` now collects `f:` claims per physical path and keeps
  `_preferred_claim`: the card whose `workspace_path` contains the file (the
  producer), tie-broken by earliest `created_at`.
- The `u:` link loop dedupes URL claims across cards with a shared `seen` set
  (same bug class — `_card_links` already deduped within a card).

## Verification

- `tests/hermes_cli/test_projects_outputs_api.py` — two new regression tests
  (cross-card path claim incl. attach-leaves-no-twin; cross-card URL claim);
  14/14 pass.
- New module run against the live MOU project on the box (loaded from /tmp,
  checkout untouched): 34 → 28 artifacts, 31 → 25 unattached drafts; the
  surviving 黃震遐_定稿v2 row is owned by the producer card `t_b976fa57`.

## Status

- [x] Implemented + regression tests
- [x] Verified against live production data
- [x] Merged (PR #496) / deployed `3f817065c` — live read now returns
  28 artifacts / 25 unattached drafts on the MOU project
