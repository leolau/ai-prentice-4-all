# Chat page perf fix + conversation categorization

User report: the agent-home Chats page loads slowly; asked for an index to
help, plus grouping conversations into "Kanban" / "Daily" / "Others".

## Performance — what was actually measured, not guessed

Ran the real chat-list query (`list_sessions_rich(order_by_last_active=True)`,
the query behind `GET /api/sessions?order=recent`, which is what the chat
page always uses) via `EXPLAIN QUERY PLAN` against the live production
database (350 sessions, ~44k messages at time of writing).

**The SQL itself was not the bottleneck at this scale** — 67ms for a
20-row page. But the plan showed `SCAN s` (full table scan) twice: no index
covered `WHERE archived = 0 AND source NOT IN (...)` together, so SQLite
scanned every row. Fine at 350 rows; linear in total session count as
history grows, which is a real latent problem worth closing now while it's
cheap.

**What's actually causing today's slowness** is in
`agent-home/src/app/chat/page.tsx`:
1. `client.profiles()` and `client.sessions()` are independent calls but
   were awaited **sequentially** — pure added latency, every page load.
2. The page always fetched **200 sessions** with `order: "recent"` (the
   expensive recency query) just to render a tab strip and open the most
   recent conversation — far more than a first paint needs.

### Fixes

1. **`hermes_state.py`**: added `idx_sessions_archived_started ON
   sessions(archived, started_at DESC)`, in `DEFERRED_INDEX_SQL` (not the
   eager `SCHEMA_SQL` block — `archived` itself is a column
   `_reconcile_columns()` backfills on legacy databases, so an index on it
   in `SCHEMA_SQL` breaks `executescript()` against a pre-`archived` DB
   before reconciliation runs; caught this by running the full
   `test_hermes_state.py` suite, not just the session-listing subset).
   Confirmed via `EXPLAIN QUERY PLAN` on a fresh DB: `SCAN` → `SEARCH ...
   USING INDEX idx_sessions_archived_started`, sort satisfied by the index
   (no separate `ORDER BY` step).
2. **`chat/page.tsx`**: `Promise.all([profiles(), sessions()])` instead of
   sequential awaits.
3. **New `lib/chat/session-limits.ts`**: `CHAT_SESSION_LIST_LIMIT = 50`,
   used by both the server-side first-paint fetch and `ChatPane`'s
   `refreshSessions()` (must match — a refresh returning fewer rows than
   first paint would visibly shrink the strip). The "All conversations"
   sheet (below) fetches its own wider set on demand instead, so nothing
   loses access to full history.

## Conversation categorization

Requested groups, clarified with the user to exactly:
- **Kanban** — sessions run by a kanban card worker.
- **Daily** — today's conversations (any origin), not otherwise categorized.
- **Scheduled** — cron-triggered sessions (a 4th group added during
  clarification — these were previously excluded from the chat list
  entirely; the new "All conversations" view is the first place they
  become visible).
- **Others** — everything else (older, non-kanban, non-cron).

Priority order matters: a kanban-worker session that happened today is
still "Kanban", not "Daily"; a cron session from today is still
"Scheduled", not "Daily".

### Design decisions

- **No new schema.** A session's existing `source` field (`cron`) and `cwd`
  field (already returned by `list_sessions_rich`'s `SELECT s.*`, just not
  previously declared on the `SessionSummary` TS type) are enough — this is
  the "extend existing code" rung of the footprint ladder, not a new
  tagging/column mechanism. The app already has a full session-tags feature
  for user-defined tagging, deliberately left alone here.
- **Kanban detection is a best-effort `cwd` substring check**
  (`/kanban/workspaces/` or `/kanban/boards/`, covering
  `kanban_db.workspaces_root()`'s default-board and named-board shapes).
  Deliberately does **not** also match `.worktrees/` for project-linked
  kanban tasks, since that path shape is shared with ordinary git worktrees
  unrelated to kanban — a false positive there would be worse than the
  false negative of missing that one subset.
- **UI is a new surface, not a retrofit of the existing strip.** The actual
  chat page shows conversations as a horizontal scrollable chip strip
  (`SessionTabs.tsx`), not a list — there was no existing vertical list to
  add section headers to. Confirmed this mismatch with the user before
  building; the agreed shape is a new "All conversations" modal (opened via
  a new "All" button in the header, next to "Archived"), which fetches its
  own conversation set (including cron, unlike the strip) and groups it
  into four collapsible `<details>` sections.

### New/changed files

- `agent-home/src/lib/chat/categorize.ts` — pure `categorizeSession()` /
  `groupSessionsByCategory()`, priority-ordered as above.
- `agent-home/src/lib/chat/categorize.test.ts` — 11 tests, including local
  calendar-day boundary behavior (constructed with local-time `Date(y,m,d)`
  args, not UTC ISO strings, so the test is timezone-portable).
- `agent-home/src/components/chat/AllConversationsSheet.tsx` — new modal,
  modeled on the existing `ArchivedModal.tsx` pattern (same fetch/loading/
  error shape); fetches `GET /api/sessions?order=recent&limit=200` (no
  source exclusion) on open, groups client-side, renders one `<details>`
  per non-empty category.
- `agent-home/src/lib/chat/header-actions.ts` — added
  `openAllConversations` to the shared ref shape (mirrors the existing
  `openArchived` bridge between the server-component page and the
  client-component header).
- `agent-home/src/components/chat/ChatHeaderActions.tsx` — new "All"
  button; existing test file extended with a matching assertion.
- `agent-home/src/components/chat/ChatPane.tsx` — wires
  `allConversationsOpen` state + the ref callback, renders the sheet,
  reuses the existing `openConversation(id)` handler for selection (no new
  switch-conversation logic).
- `agent-home/src/types/index.ts` — added `cwd?: string | null` to
  `SessionSummary` (the field was already in the API response; just not
  previously declared in the type).

## Verification

- `python -m pytest tests/test_hermes_state.py
  tests/test_hermes_state_compression_locks.py
  tests/test_hermes_state_wal_fallback.py` — 331 passed.
- `python -m pytest tests/hermes_cli/test_web_server.py
  tests/hermes_cli/test_web_server_tags.py
  tests/hermes_cli/test_web_server_session_search.py` — 363 passed, 1
  pre-existing unrelated failure (`croniter` package missing locally for a
  cron-blueprint test, nothing to do with sessions).
- `npx tsc --noEmit` (agent-home) — clean.
- `npx vitest run` across `src/lib/chat/` and the two touched chat
  components — 68 passed; the only failure in the full run is the
  pre-existing, documented `html-encoding-sniffer`/`ERR_REQUIRE_ESM` jsdom
  environment issue unrelated to this change.
- Verified the new index is actually used: `EXPLAIN QUERY PLAN` on a fresh
  `SessionDB` shows `SEARCH s USING INDEX idx_sessions_archived_started
  (archived=?)` for the exact filter shape used by the chat list query.

## Addendum (same day, follow-up PR): inline grouping, no modal

User feedback after the above shipped: the "All conversations" modal
worked, but wasn't user-friendly — an extra click and a whole separate
window just to see conversations grouped, when the point was to make
grouping visible immediately.

**Replaced the modal with inline grouped rows in the strip itself.**
`SessionTabs.tsx` now renders one labelled, independently
horizontally-scrollable row per non-empty category (Kanban / Daily /
Scheduled / Others) instead of a single flat row — no extra click, no
separate surface. `AllConversationsSheet.tsx` and the "All" header button
are removed; their purpose is now served by the strip directly.

This required widening what the strip's own data actually contains: it
was fetching with `excludeSources: "cron"` (both the server-side first
paint in `chat/page.tsx`, and the client-side refresh in `ChatPane`, which
goes through the `GET /api/chat/sessions` BFF route). The BFF route itself
also *hardcoded* `excludeSources: "cron"` with no way for a caller to
override it — found this while wiring the refresh path, not before. Fixed
by making it caller-controlled: `exclude_sources` absent keeps the
historical default (cron hidden) for the route's other two callers
(`useChatUnread`'s badge count, `ArchivedModal`) who have no reason to
care about scheduler noise; an explicit `exclude_sources=` (empty) opts
in to seeing everything, which is what `ChatPane`'s refresh now sends.

**Drag-to-reorder** is scoped to within a category's own row: dragging
reorders that category's ids among themselves, then the new global order
is rebuilt by splicing the reordered ids back into their original global
positions (every other category's ids stay exactly where they were).
Reordering a session *into* a different category isn't supported — the
category is derived from the session's own data, not a manual grouping,
so there is nothing to actually change by moving it.

### Files touched (this addendum)

- `agent-home/src/components/chat/SessionTabs.tsx` — rewritten to group
  and render multiple rows; drag/drop reworked to be per-category.
- `agent-home/src/components/chat/SessionTabs.test.tsx` — rewritten:
  asserts `data-category="..."` markers per row, empty categories omitted
  entirely, still no "Archived"/"+ New" leakage, still scrollable.
- `agent-home/src/components/chat/AllConversationsSheet.tsx` — deleted.
- `agent-home/src/lib/chat/header-actions.ts`,
  `ChatHeaderActions.tsx` (+ its test) — `openAllConversations`/"All"
  button removed.
- `agent-home/src/components/chat/ChatPane.tsx` — removed the sheet's
  state/ref-wiring/render; `refreshSessions()` now sends
  `exclude_sources=` (empty) explicitly.
- `agent-home/src/app/api/chat/sessions/route.ts` — `exclude_sources` is
  now caller-controlled (absent = default "cron", present = caller's
  value, including empty-string "include everything").
- `agent-home/src/app/chat/page.tsx` — dropped `excludeSources: "cron"`
  from the first-paint fetch (this one calls the Python API directly via
  `apiClientForRequest`, not through the BFF route, so it needed its own
  fix independent of the route change above).

### Verification (this addendum)

- `npx tsc --noEmit` — clean.
- `npx vitest run` across `src/lib/chat/` + the touched chat components —
  73 passed (up from 68; the two rewritten `SessionTabs` tests plus one
  new "omits empty categories" test), same single pre-existing
  unrelated jsdom failure as before.
- `npx next build` — clean, `/chat` route still builds.
- Confirmed no leftover references to the deleted component/callback
  (`grep -rl "AllConversationsSheet\|openAllConversations" src/` — empty).

## Addendum 2 (same day, follow-up PR): a real regression, fixed with a dropdown

User report: a conversation titled "Andrew AL" had gone missing entirely
— not miscategorized, just absent from the whole strip. Verified against
production before assuming anything: it ranked **#65** by recency, past
`CHAT_SESSION_LIST_LIMIT` (lowered to 50 in the first addendum's perf
pass). That fetch limit was the actual bug — the session never reached
the categorization logic at all, since it wasn't in the fetched set to
begin with.

Root cause traced to a genuine tension: the perf fix intentionally
lowered the fetch size to keep first paint fast, on the assumption that
the strip only ever shows a handful of chips. That assumption broke the
moment grouping was added — grouping needs the *whole* recent set to
sort correctly into categories, not just the top 50.

**Fix, requested directly by the user: replace the four stacked rows with
a "Category" dropdown that renders only the selected category's row.**
This resolves the tension outright — a single visible row doesn't care
how wide the underlying fetch is, so the limit could go back to 200 (the
backend's own hard cap) without the page getting any taller. Categories
with zero conversations aren't offered as dropdown options.

The dropdown defaults to whichever category the *active* conversation is
actually in (kanban session active → dropdown opens on "Kanban"), and
re-syncs only when the active conversation itself changes (e.g. opened
via a citation) — a manual dropdown switch is never fought by an
unrelated re-render.

### Files touched (this addendum)

- `agent-home/src/lib/chat/session-limits.ts` — `CHAT_SESSION_LIST_LIMIT`
  back to 200 (the BFF route's own ceiling), with the doc comment
  rewritten to explain *why* 50 was wrong, not just what the value is —
  the wrong assumption ("only ever shows a handful of chips") was the
  actual bug, and a future editor re-lowering this for "perf" would
  reintroduce the exact same missing-conversation report.
- `agent-home/src/app/chat/page.tsx` — the `requested`-session fallback
  fetch now reuses `CHAT_SESSION_LIST_LIMIT` instead of a second
  hardcoded `200` (no behavior change, just one fewer place to forget to
  update together).
- `agent-home/src/components/chat/SessionTabs.tsx` — rewritten again:
  `<select>` (styled to match `ProfilePicker`'s existing dropdown) plus
  one row for the selected category only, instead of one row per
  category stacked. Drag-to-reorder logic is unchanged in spirit (still
  scoped to the visible category, still rebuilt by splicing into the
  global order) — just operating on the single visible `items` array
  instead of iterating every category.
- `agent-home/src/components/chat/SessionTabs.test.tsx` — rewritten: the
  category-membership assertions from before still apply, plus new
  assertions that only the *selected* category's chips render (not
  every category's, simultaneously) and that the dropdown itself is
  absent when there is nothing to categorize.

### Verification (this addendum)

- `npx tsc --noEmit` — clean.
- `npx vitest run` — 71 passed (7 for the rewritten `SessionTabs`, same
  count of assertions reshaped for dropdown behavior), same single
  pre-existing unrelated jsdom failure as both prior addenda.
- `npx next build` — clean.
- Verified the actual regression against production data directly (not
  just reasoning about it): confirmed "Andrew AL"'s rank (65th) before
  writing any fix, and that the fetch limit — not categorization — was
  the mechanism.

## Addendum 3 (same day, follow-up PR): manual category override

User request: let the user actually move a conversation between
categories from the conversation details popup (`SessionModal`), not just
view its derived one.

**Storage: reused the existing session-tags system rather than a new
column.** A manual override is a reserved tag named `category:<value>`
(e.g. `category:kanban`), attached/removed through the exact same
`add_tag_to_session`/`remove_tag_from_session` machinery the Tags section
already uses — no schema change, matching this repo's "extend existing
code before adding a column" convention. `categorizeSession()` checks for
this override first, before any of the derived signals (kanban `cwd`,
`cron` source, same-day) — the whole point of a manual move is that it
stops being auto-derived.

These reserved tags are deliberately **hidden from the generic Tags UI**
(`isCategoryOverrideTag()` filters them out of both the tag-chip list and
the "Associate tag…" picker in `SessionModal`) — they're an
implementation detail, surfaced only through the dedicated Category
field, not something a user should see or manage as a regular tag.

**A real gap found while wiring this up**: the bulk conversation list
(`GET /api/sessions`, what `SessionTabs`'s grouping actually reads) never
attached each row's tags — only the single-session detail fetch used by
the modal did. Without fixing that, an override would only ever be
visible *after* opening the modal for that specific conversation, never
in the grouped strip itself. Added `SessionDB.get_tags_for_sessions()` (one
bulk query, not N) and wired it into the list route so every row carries
its `tags` from the start.

### Files touched (this addendum)

- `hermes_state.py` — new `get_tags_for_sessions(session_ids)`, a single
  `WHERE session_id IN (...)` query; sessions with no tags are simply
  absent from the returned dict rather than mapped to `[]`.
- `hermes_cli/web_server.py` — `get_sessions` (`GET /api/sessions`) now
  bulk-attaches `tags` to every row.
- `tests/test_session_tags.py`, `tests/hermes_cli/test_web_server_tags.py`
  — new tests for the bulk method and the route-level attach.
- `tests/hermes_cli/test_web_server.py` — a pre-existing fake `SessionDB`
  stub in `test_get_sessions_forwards_min_messages` needed a
  `get_tags_for_sessions` method added; without it the route now 500s
  against that stub. Found by running the full suite, not assumed.
- `agent-home/src/lib/chat/categorize.ts` — new `categoryOverrideTagName`,
  `isCategoryOverrideTag`, `categoryOverride`; `categorizeSession` checks
  the override first.
- `agent-home/src/components/chat/SessionModal.tsx` — new Category field
  (a `<select>`, positioned right after Name); filters reserved tags out
  of the Tags section and the tag-association picker.
- `agent-home/src/components/chat/ChatPane.tsx` — new
  `setSessionCategory()`: removes any existing override tag, adds the new
  one, then refreshes the strip so it regroups immediately rather than
  waiting for the next natural refresh. Computes the modal's `category`
  prop from `categorizeSession()` over the currently-loaded session-tags
  state, consistent with how the Tags section itself already sources its
  data (same brief loading flash while that fetch resolves, no new
  regression).

### Verification (this addendum)

- `pytest tests/test_session_tags.py tests/hermes_cli/test_web_server_tags.py
  tests/test_hermes_state.py tests/hermes_cli/test_web_server.py
  tests/hermes_cli/test_web_server_session_search.py` — 693 passed, 1
  pre-existing unrelated failure (`croniter`).
- `npx tsc --noEmit` — clean.
- `npx vitest run` — 105 passed across the touched chat files (up from
  71: 10 new `categorize.ts` tests for the override, 5 new `SessionModal`
  tests for the Category field and tag-hiding), same single pre-existing
  unrelated jsdom failure.
- `npx next build` — clean.

## Not done / explicitly out of scope

- Did not rewrite the recency query's recursive-CTE architecture. At
  production's current scale it's fast; if session count grows enough that
  the (now index-assisted) full scan itself becomes the bottleneck again,
  the real fix is maintaining a denormalized "effective last active"
  column on each root session (updated on new message / compression
  continuation) so `ORDER BY ... LIMIT` can be satisfied by an index
  without materializing every matching session's chain first. Left as a
  documented future option, not built speculatively.
- Did not touch the existing session-tags feature — categorization here is
  derived, not stored, and doesn't compete with user-defined tags.
