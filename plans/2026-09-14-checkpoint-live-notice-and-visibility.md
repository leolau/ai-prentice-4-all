# Checkpoint holds: live push notice + always-visible "what's waiting" (§7.1/§12)

## Incident

Project `yot-madam-lau-wong-fat-tender-t20252026-008`'s run correctly
finished a checkpoint card (`draft-pricing-schedule`, supervised autonomy)
and held its successors in triage — by design, so nothing drafts around
numbers a human hasn't reviewed. The worker left a detailed comment with
5 concrete questions. Nobody saw it: there was no push notification, and
after 2 hours the stale-run sweep (Gap B) auto-failed the run with
`"orphaned by a process restart or a stuck dependency chain"` — which was
wrong; it was correctly waiting on a person, not orphaned.

The user's ask, verbatim: (1) push a notification to the PWA with a link
straight to answer the questions; (2) even with no reply, the UI must
always show what's happening, which card needs answers, what the
questions are, and a way to answer them — the run page showed none of
this.

## Root causes

1. **No live notification.** `raise_approval()` was only ever called once,
   inside `start_run()`, based on the *static* playbook ("this method has
   checkpoints somewhere") — before anyone could know whether or when one
   would actually be reached. Nothing fired when the checkpoint genuinely
   engaged, hours later.
2. **No push at all.** Even that one notice only ever reached the in-app
   notification bell — `NotificationStore.create()` has no push wiring;
   a person has to have the app open to see it.
3. **The stale-run sweep didn't know about checkpoints.** `_reconcile_one_run`
   only checked "has anything happened in `run_stall_seconds`?" — a
   legitimately-waiting checkpoint looks identical to a genuinely dead run
   by that measure, so it auto-failed a run that was working exactly as
   designed.
4. **The run page showed a boolean, not the content.** `awaiting_continue`
   told the page *that* something was held, never *what* — the checkpoint
   card's own comment (the actual questions) was never surfaced; a person
   had to already know to click through to the card to find them.

## Fix

**`hermes_cli/projects_run.py`**:
- `checkpoint_wait_info(conn, project, run, cards)` — moved and enriched
  from `projects_api.py`'s private `_run_awaits_continue`: now returns
  *which* checkpoint card is the reason and *which* successor(s) are held,
  not just a boolean. `run_awaits_continue(...)` is the cheap boolean form
  kept for anything that only needs that.
- `raise_approval()` gained `dedupe_suffix` (so more than one checkpoint in
  one run gets distinct notifications, not one collapsed onto the other)
  and `url` (the push's deep link, defaulting to the run's own page), plus
  a best-effort push send alongside the existing durable approval row —
  reusing the app platform's existing `send_push` (VAPID + device
  subscriptions already live there for chat delivery; nothing new to
  build). A push failure or missing enrollment never blocks the approval
  itself.
- `notify_if_awaiting_checkpoint(pconn, bconn, *, project, run)` — the new
  entry point: detects the *live* hold and raises the notice quoting the
  checkpoint card's own comment (or its run summary if it left no
  comment). Safe to call on every refill/sweep tick — dedup happens at
  the approval-store layer, per run *and* per checkpoint step.

**`hermes_cli/projects_reconcile.py`**:
- `on_card_settled` (Gap A's immediate hook — fires the instant a card
  completes) now calls `notify_if_awaiting_checkpoint` right after a
  refill finds nothing to promote — near-instant notice instead of
  waiting on the periodic sweep.
- `_reconcile_one_run` (the periodic sweep, Gap B) checks the same thing
  as a safety net *before* the staleness check, and — this is the actual
  bug fix — **returns without failing the run** when a checkpoint is
  genuinely held. A checkpoint hold has no timeout; it waits on a human
  for however long that takes.

**`hermes_cli/projects_api.py`**: `_run_payload` now attaches
`checkpoint_wait` (the checkpoint card's id/title/comment + the held
card ids) alongside the existing `awaiting_continue` boolean, so the run
page can render the real content instead of guessing from a flag.

**`agent-home`**:
- `types/index.ts` — `ProjectRunCheckpointWait`, added to `ProjectRun`.
- `RunView.tsx` — the next-action panel now quotes the checkpoint's own
  comment, links straight to the checkpoint card, and lists the held
  successor card(s) with a pointer to answer inside their brief (Edit
  card → Make ready is the existing mechanism; no new UI needed there).
  The now-redundant `CheckpointBanner` (a second, generic "the checkpoint
  is done" line) was removed — one clear panel instead of two competing
  ones, continuing the consolidation started alongside PR #403.

## Explicitly not built

- No new "answer the checkpoint" comment-reply UI. The existing blocked-
  card convention — edit the card's brief, then release it — already
  covers "leave your answer somewhere the next worker will read it";
  reusing it keeps this change additive rather than inventing a second
  mechanism for the same idea.
- No structured question/answer schema. The worker's comment is free
  text (exactly what it already writes); quoting it verbatim is the
  whole fix — parsing it into discrete Q&A fields would be a much larger,
  much more fragile change for no proven benefit yet.
