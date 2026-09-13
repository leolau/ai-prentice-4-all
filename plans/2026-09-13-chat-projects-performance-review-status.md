# Implementation status: Chat + projects performance review

Companion to `plans/2026-09-13-chat-projects-performance-review.md`.

| # | Area | Description | Status | PR / commit | Notes |
|---|------|-------------|--------|--------------|-------|
| 1 | Chat | Memoize `MessageBubble` so a streaming turn (per-token + 1s clock tick re-renders of `ChatPane`) doesn't re-run the media-segment regex and re-parse Markdown for every *other* message in the thread | **Done** | uncommitted in working tree — `src/components/chat/MessageBubble.tsx` | Verified with `npm run typecheck` (agent-home workspace); `vitest` blocked by a pre-existing, unrelated `@rolldown/binding-darwin-arm64` native-binding install failure in this worktree |
| 2 | Chat | Virtualize the message thread for very long single-conversation transcripts | **Not started** | — | Deliberately out of scope for this pass — no evidence yet that any real transcript is large enough to need it; revisit if reported |
| 3 | Projects | List (`/projects`), detail (`/projects/[slug]`), and live-update poller (`useProjectEvents`) | **No changes — reviewed, no material issues found** | — | Already keyset-paginated, debounced, visibility-gated polling; see plan doc |

## Re-check (2026-09-13, after later fixes landed on `develop`/`main`)

Re-verified because unrelated fixes (project-run resilience: refill,
restart recovery, doctor stalled-run reporting — PRs #386–#389, in
`gateway/kanban_watchers.py`, `gateway/run.py`, `hermes_cli/projects_run.py`,
`hermes_cli/projects_reconcile.py`, `hermes_cli/projects_schedule.py`) had
been checked in upstream in the meantime.

- `git diff --stat HEAD origin/main -- agent-home` and
  `git diff --stat HEAD origin/develop -- agent-home` are both **empty** —
  none of the newly checked-in fixes touch `agent-home` at all; they're
  backend-only (Python `hermes_cli`/`gateway`). The chat and projects UI
  code is unchanged from the state this review was originally done against.
- Attempted `git merge origin/develop` to pull the new fixes in anyway (in
  case something adjacent mattered); it produced Python-side conflicts in
  `hermes_cli/projects_reconcile.py` / `tests/hermes_cli/test_projects_run.py`
  unrelated to this review's scope, so the merge was aborted rather than
  resolved blind. Bringing those backend fixes into this branch, if wanted,
  should be its own task.
- Confirmed `git log --all -- agent-home/src/components/chat/MessageBubble.tsx`
  and `.../projects/*` show no other commit (on any branch) ever added
  memoization to `MessageBubble` or changed the reviewed projects files —
  row #1's fix is still the first and only fix for the finding.
- Row #1's fix (`MessageBubble` wrapped in `memo`) is still present,
  unchanged, and still uncommitted in the working tree. Re-ran
  `npm run typecheck` (agent-home workspace) — still passes.

**Conclusion: no revision needed.** The finding and fix in row #1 stand as
originally written; row #3 (projects) still has no material issues.

## How to update this file

When row #1 is committed, fill in the commit hash. If virtualization (#2)
is picked up later, flip its status and link the plan/PR that scopes it.
