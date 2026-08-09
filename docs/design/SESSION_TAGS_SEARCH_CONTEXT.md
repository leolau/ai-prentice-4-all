# Session Tags, Search & Context Window — Design Plan

## Overview

Five features for the agent-home chat surface (`/chat`), touching the
`SessionModal` popup, the `SessionTabs` strip, and the `ChatPane` thread:

1. **Context window size** in the session detail popup + collapse button
2. **Tagging system** across all sessions (manual + LLM auto-tag suggestions)
3. **Tag-based filtering** with AND / OR / NOT operators
4. **Cross-session keyword search** with next/previous navigation
5. **In-session keyword search** with next/previous navigation

All five target the agent-home Next.js app (`agent-home/src/`), with new
Python API endpoints in `hermes_cli/web_server.py` and new BFF routes under
`agent-home/src/app/api/chat/`.

---

## Current Architecture (as studied)

### Agent-home BFF pattern

```
Browser (ChatPane.tsx)
  │  fetch("/api/chat/*")
  ▼
Next.js BFF route (agent-home/src/app/api/chat/*/route.ts)
  │  HermesApiClient → Python API
  ▼
Python API (hermes_cli/web_server.py — /api/sessions/*)
  │  SessionDB
  ▼
SQLite state.db (hermes_state.py)
```

The browser never calls the Python API directly. Every request goes through
a Next.js BFF route that forwards to the Python API via `HermesApiClient`.

### Current session UI components

| Component | File | Role |
|-----------|------|------|
| `ChatPane` | `agent-home/src/components/chat/ChatPane.tsx` (521 lines) | Main chat orchestrator — holds sessions list, messages, modal state |
| `SessionTabs` | `agent-home/src/components/chat/SessionTabs.tsx` (141 lines) | Horizontal scrollable strip of conversation chips |
| `SessionModal` | `agent-home/src/components/chat/SessionModal.tsx` (176 lines) | Popup when tapping active chip — rename + stats |
| `ArchivedModal` | `agent-home/src/components/chat/ArchivedModal.tsx` (129 lines) | Archived conversations popup |
| `MessageBubble` | `agent-home/src/components/chat/MessageBubble.tsx` (103 lines) | Single message render — user right, assistant left |

### Current data types

```typescript
// agent-home/src/types/index.ts
interface SessionSummary {
  id: string;
  source: string;
  title: string | null;
  preview: string | null;
  message_count: number;
  started_at: number | null;
  last_active: number | null;
  ended_at: number | null;
  is_active?: boolean;
  archived?: boolean;
  // NO token fields, NO tags
}

interface ChatMessage {
  id?: number;
  role: ChatRole;
  content: string;
  timestamp?: number | string | null;
  // NO token_count
}
```

### Current Python API endpoints

| Method | Path | Used by agent-home? |
|--------|------|---------------------|
| `GET` | `/api/sessions` | Yes (via BFF) |
| `GET` | `/api/sessions/{id}` | No |
| `PATCH` | `/api/sessions/{id}` | Yes (via BFF rename/archive) |
| `GET` | `/api/sessions/{id}/messages` | Yes (via BFF) |
| `GET` | `/api/sessions/search?q=...` | **No** (exists for web dashboard only) |

The search endpoint exists in the Python API but agent-home doesn't use it.

### SQLite schema (relevant tables)

```sql
-- sessions table (existing, schema v17)
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  ...
  message_count INTEGER DEFAULT 0,
  tool_call_count INTEGER DEFAULT 0,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cache_read_tokens INTEGER DEFAULT 0,
  cache_write_tokens INTEGER DEFAULT 0,
  reasoning_tokens INTEGER DEFAULT 0,
  ...
  archived INTEGER DEFAULT 0
);

-- messages table (existing)
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT,
  ...
  token_count INTEGER,
  ...
);

-- FTS5 virtual tables (existing)
CREATE VIRTUAL TABLE messages_fts USING fts5(...);
CREATE VIRTUAL TABLE messages_fts_trigram USING fts5(..., tokenize='trigram');
```

No tag tables exist yet.

---

## Feature 1: Context Window Size in Session Detail Popup

### Goal

In the `SessionModal` popup (opened by tapping the active session chip), show:
- The session's total token usage (input + output + cache + reasoning)
- A collapsible "Context Window" section with a breakdown bar
- A collapse/expand button to hide/show the section

### Design

#### Data: extend `SessionSummary` with token fields

The Python API's `/api/sessions` endpoint already returns token data (the
web dashboard's `SessionInfo` type includes `input_tokens` and
`output_tokens`). The agent-home `SessionSummary` type just doesn't include
them. Add:

```typescript
// agent-home/src/types/index.ts — extend SessionSummary
interface SessionSummary {
  // ... existing fields ...
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  reasoning_tokens?: number;
}
```

No Python-side change needed — the data is already in the response; the TS
type was just omitting it. Verify the BFF route at
`agent-home/src/app/api/chat/sessions/route.ts` passes these fields through
(it currently does `NextResponse.json(data)` on the raw response, so it does).

#### UI: SessionModal changes

In `SessionModal.tsx`, add a collapsible "Context Window" section between the
name input and the Statistics section:

```
┌─────────────────────────────────────┐
│ Conversation                  Close │
│                                     │
│ Name                                │
│ [___________________________]       │
│                                     │
│ ▼ Context Window              [▼]  │ ← collapse button
│ ┌─────────────────────────────────┐ │
│ │ Total tokens: 45,230            │ │
│ │ ████████████░░░░░░  68% used    │ │
│ │                                 │ │
│ │ Input:     32,000               │ │
│ │ Output:     8,500               │ │
│ │ Cache read:  3,200              │ │
│ │ Cache write:   530              │ │
│ │ Reasoning:   1,000              │ │
│ └─────────────────────────────────┘ │
│                                     │
│ Statistics                          │
│ Messages          42                │
│ Source            agent_home        │
│ Status            Active            │
│ ...                                │
└─────────────────────────────────────┘
```

When collapsed, the section shows only the header + button:
```
│ ▶ Context Window              [▶]  │
```

Implementation:
- Add `const [ctxCollapsed, setCtxCollapsed] = useState(false)` state
- Compute `totalTokens = input + output + cache_read + cache_write + reasoning`
- Show a simple bar (CSS `div` with width % based on a notional 128K context
  limit, or just show raw tokens without a percentage since we don't know
  the model's context window from the session alone)
- The collapse button toggles `ctxCollapsed`

#### Files to modify

| File | Change |
|------|--------|
| `agent-home/src/types/index.ts` | Add token fields to `SessionSummary` |
| `agent-home/src/components/chat/SessionModal.tsx` | Add collapsible context window section |

---

## Feature 2: Tagging System

### Goal

- User can see a list of all tags
- User can add and remove tags on a session
- System auto-suggests tags via LLM analysis
- System asks the user to confirm or dismiss suggested tags
- User can untag (remove a tag from a session)

### Design

#### SQLite schema (new tables, migration v18)

```sql
-- Tag definitions (per-profile, like sessions)
CREATE TABLE IF NOT EXISTS session_tags (
  id TEXT PRIMARY KEY,           -- UUID
  name TEXT NOT NULL UNIQUE,      -- "deployment", "bug-fix", etc.
  color TEXT DEFAULT 'blue',     -- visual tag color
  created_at REAL NOT NULL,
  -- Tags are per-profile (stored in the same state.db)
  -- No user_id needed for single-principal profile model
);

-- Tag-to-session mapping (many-to-many)
CREATE TABLE IF NOT EXISTS session_tag_map (
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL REFERENCES session_tags(id) ON DELETE CASCADE,
  source TEXT DEFAULT 'manual',  -- 'manual' | 'auto-suggested' | 'auto-confirmed'
  suggested_at REAL,             -- when LLM suggested it (if auto)
  confirmed_at REAL,             -- when user confirmed it (if auto)
  created_at REAL NOT NULL,
  PRIMARY KEY (session_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_session_tag_map_tag ON session_tag_map(tag_id);
CREATE INDEX IF NOT EXISTS idx_session_tag_map_session ON session_tag_map(session_id);
```

The `source` field tracks whether a tag was:
- `manual` — user added it directly
- `auto-suggested` — LLM suggested it, user hasn't responded yet
- `auto-confirmed` — user confirmed the LLM's suggestion

#### Python API endpoints (new, in `hermes_cli/web_server.py`)

```
GET    /api/sessions/tags                    — list all tags
POST   /api/sessions/tags                    — create a tag {name, color}
DELETE /api/sessions/tags/{tag_id}           — delete a tag (cascades to map)

GET    /api/sessions/{id}/tags               — list tags for a session
POST   /api/sessions/{id}/tags               — add tag to session {tag_id}
DELETE /api/sessions/{id}/tags/{tag_id}      — remove tag from session
POST   /api/sessions/{id}/tags/suggest       — LLM suggests tags for this session
```

**`GET /api/sessions/tags`** returns:
```json
{
  "tags": [
    {"id": "...", "name": "deployment", "color": "blue", "session_count": 5},
    {"id": "...", "name": "bug-fix", "color": "red", "session_count": 12}
  ]
}
```

**`POST /api/sessions/{id}/tags/suggest`** — auto-tagging endpoint:
1. Loads the session's messages (last N messages, capped at ~4K tokens)
2. Loads the existing tag names (so the LLM can match to existing tags)
3. Sends a DeepSeek call with a system prompt asking to classify the session
   into existing tags or propose new ones
4. Returns suggested tag names (not yet applied — user must confirm)
5. Suggestions are stored as `source='auto-suggested'` in `session_tag_map`

**DeepSeek prompt for auto-tagging:**

```
You are a session tagger. Analyze this conversation and suggest tags.

Existing tags: deployment, bug-fix, refactoring, research, planning

Rules:
- Return JSON: {"suggested_tags": ["tag1", "tag2"], "new_tags": [{"name": "new-tag", "reason": "..."}]}
- suggested_tags must be from the existing tags list (match by meaning)
- new_tags are for topics not covered by existing tags
- Suggest 1-3 tags total — only the most relevant
- Use lowercase kebab-case for new tag names

Conversation:
{messages_summary}
```

#### BFF routes (new, in `agent-home/src/app/api/chat/`)

```
GET    /api/chat/sessions/tags               — list all tags
POST   /api/chat/sessions/tags               — create a tag
DELETE /api/chat/sessions/tags/[tagId]       — delete a tag

GET    /api/chat/sessions/[sessionId]/tags   — list session tags
POST   /api/chat/sessions/[sessionId]/tags   — add tag to session
DELETE /api/chat/sessions/[sessionId]/tags/[tagId] — remove tag
POST   /api/chat/sessions/[sessionId]/tags/suggest — trigger LLM auto-tag
```

Each follows the existing BFF pattern: resolve principal → forward to Python
API via `HermesApiClient` → return JSON.

#### API client methods (new, in `agent-home/src/lib/api/client.ts`)

```typescript
async sessionTags(): Promise<SessionTagsResponse>
async createSessionTag(name: string, color?: string): Promise<SessionTag>
async deleteSessionTag(tagId: string): Promise<void>
async sessionTagsForSession(sessionId: string): Promise<SessionTag[]>
async addSessionTag(sessionId: string, tagId: string): Promise<void>
async removeSessionTag(sessionId: string, tagId: string): Promise<void>
async suggestSessionTags(sessionId: string): Promise<TagSuggestion[]>
```

#### Types (new, in `agent-home/src/types/index.ts`)

```typescript
interface SessionTag {
  id: string;
  name: string;
  color: string;
  session_count?: number;
}

interface TagSuggestion {
  tag_name: string;
  is_new: boolean;      // true if the tag doesn't exist yet
  reason?: string;     // LLM's reason for suggesting it
  confidence?: number;  // 0-1
}

// Extend SessionSummary
interface SessionSummary {
  // ... existing ...
  tags?: SessionTag[];
}
```

#### UI components

**Tag chips in SessionTabs:** Each session chip shows small tag dots below
the title when tags are present.

**Tag section in SessionModal:** Between name and context window:

```
│ Tags                          [+]  │
│ [deployment] [bug-fix]            │
│                                     │
│ ┌─ Suggested ────────────────────┐ │
│ │ 💡 refactoring (87% confident)  │ │
│ │    [Accept] [Dismiss]           │ │
│ └─────────────────────────────────┘ │
```

- `[+]` button opens a tag picker (list of all tags + create new)
- Clicking a tag chip removes it (with confirm)
- Suggested tags show an accept/dismiss call-to-action
- When a new session turn completes and the session has no tags, the system
  can call `suggestSessionTags` in the background and surface suggestions in
  the SessionModal next time it's opened

**Auto-tag trigger:** After a session's first turn completes (in
`ChatPane.send()` callback), if the session has no tags and has ≥ 3
messages, call `POST /api/sessions/{id}/tags/suggest` in the background.
Store suggestions as `auto-suggested`. When the user next opens the
SessionModal, show the suggestions with accept/dismiss buttons.

**Tag list modal:** A separate modal (from ChatHeaderActions or a new nav
item) that shows all tags with session counts, and lets the user
create/delete/rename tags.

#### Files to create/modify

| File | Action |
|------|--------|
| `hermes_state.py` | Add tag tables to schema v18 migration; add tag CRUD methods |
| `hermes_cli/web_server.py` | Add `/api/sessions/tags/*` and `/api/sessions/{id}/tags/*` endpoints |
| `agent-home/src/types/index.ts` | Add `SessionTag`, `TagSuggestion` types; extend `SessionSummary` |
| `agent-home/src/lib/api/client.ts` | Add tag CRUD + suggest methods |
| `agent-home/src/app/api/chat/sessions/tags/route.ts` | Create — BFF list/create tags |
| `agent-home/src/app/api/chat/sessions/tags/[tagId]/route.ts` | Create — BFF delete tag |
| `agent-home/src/app/api/chat/sessions/[sessionId]/tags/route.ts` | Create — BFF list/add session tags |
| `agent-home/src/app/api/chat/sessions/[sessionId]/tags/[tagId]/route.ts` | Create — BFF remove session tag |
| `agent-home/src/app/api/chat/sessions/[sessionId]/tags/suggest/route.ts` | Create — BFF auto-tag suggest |
| `agent-home/src/components/chat/SessionModal.tsx` | Add tag section + suggested-tag UI |
| `agent-home/src/components/chat/SessionTabs.tsx` | Show tag dots on chips |
| `agent-home/src/components/chat/ChatPane.tsx` | Wire auto-tag trigger after first turn |
| `agent-home/src/components/chat/TagPicker.tsx` | Create — tag picker dropdown |
| `agent-home/src/components/chat/TagListModal.tsx` | Create — all-tags management modal |

---

## Feature 3: Tag-Based Filtering with AND/OR/NOT

### Goal

Filter the session list to show only sessions matching specific tag criteria:
- **AND**: sessions with ALL of the specified tags
- **OR**: sessions with ANY of the specified tags
- **NOT**: sessions WITHOUT the specified tag

### Design

#### Python API endpoint

```
GET /api/sessions?tags=deployment+bug-fix&tags_op=and&tags_negate=refactoring
```

Query parameters:
- `tags` — comma-separated list of tag names to include
- `tags_op` — `and` (default) or `or` — how to combine the include list
- `tags_negate` — comma-separated list of tag names to exclude

SQL implementation (in `list_sessions_rich` or a wrapper):

```sql
-- AND: sessions that have ALL specified tags
SELECT s.id FROM sessions s
WHERE s.id IN (
  SELECT session_id FROM session_tag_map
  WHERE tag_id IN (...)
  GROUP BY session_id
  HAVING COUNT(DISTINCT tag_id) = N  -- N = number of include tags
)
AND s.id NOT IN (
  SELECT session_id FROM session_tag_map
  WHERE tag_id IN (...)  -- negate tags
)
```

For **OR**, change `HAVING COUNT(DISTINCT tag_id) = N` to `HAVING COUNT(DISTINCT tag_id) >= 1`.

#### BFF route

Extend the existing `GET /api/chat/sessions` route to pass through tag
filter parameters:

```
GET /api/chat/sessions?tags=deployment,bug-fix&tags_op=and&tags_negate=research
```

#### UI: Tag filter bar

A horizontal scrollable bar of tag chips above the SessionTabs strip (or
integrated into it). Each chip has three states: off, include (highlighted),
exclude (struck-through). An "AND/OR" toggle switches the combination mode.

```
┌──────────────────────────────────────────────────────┐
│ Filter: [deployment ✓] [bug-fix ✓] [research ✗]  AND │
│         [+ Add tag filter]                    [Clear]  │
└──────────────────────────────────────────────────────┘
```

When a filter is active, the SessionTabs strip shows only matching sessions.
A count badge shows "12 of 45 conversations".

#### Files to create/modify

| File | Action |
|------|--------|
| `hermes_state.py` | Add tag-filter logic to `list_sessions_rich` |
| `hermes_cli/web_server.py` | Pass tag filter params from query string |
| `agent-home/src/app/api/chat/sessions/route.ts` | Pass tag filter params through |
| `agent-home/src/components/chat/TagFilterBar.tsx` | Create — tag filter UI |
| `agent-home/src/components/chat/ChatPane.tsx` | Wire TagFilterBar state |
| `agent-home/src/lib/chat/tag-filter.ts` | Create — tag filter state logic |

---

## Feature 4: Cross-Session Keyword Search with Next/Previous

### Goal

A search bar that searches across ALL sessions (not just the current one).
Results show matching sessions with snippets. When a session is opened, the
search keyword highlights all matches in the transcript, and next/previous
buttons navigate between match positions.

### Design

#### Python API

The `/api/sessions/search?q=...` endpoint already exists in
`hermes_cli/web_server.py` (line 4970) with FTS5 search and lineage dedup.
No Python-side change needed — just wire it up from agent-home.

#### BFF route (new)

```
GET /api/chat/sessions/search?q=...
```

Forwards to Python API `/api/sessions/search?q=...` via `HermesApiClient`.

#### API client method (new)

```typescript
async searchSessions(q: string): Promise<SessionSearchResult[]>
```

```typescript
interface SessionSearchResult {
  session_id: string;
  snippet: string;
  role: string | null;
  source: string | null;
  model: string | null;
  session_started: number | null;
}
```

#### UI: Search bar + results

A search bar appears in the chat header (via `ChatHeaderActions`). When the
user types, debounced search results appear in a dropdown:

```
┌──────────────────────────────────────┐
│ 🔍 deploy ECS              [✕]      │
├──────────────────────────────────────┤
│ ┌──────────────────────────────────┐ │
│ │ Deployment troubleshooting       │ │
│ │ ...deploying to ECS via aliyun... │ │
│ │ 3h ago · 12 msgs                 │ │
│ ├──────────────────────────────────┤ │
│ │ ECS instance cost analysis       │ │
│ │ ...the deploy script runs...      │ │
│ │ 2d ago · 8 msgs                  │ │
│ └──────────────────────────────────┘ │
└──────────────────────────────────────┘
```

Clicking a result opens that session and highlights the search term. The
search query is passed to `ChatPane` as `searchQuery`, which propagates to
`MessageBubble` for inline highlighting.

#### Next/Previous navigation

When a search query is active and the session is open, a floating nav bar
appears at the top of the thread:

```
┌──────────────────────────────────────────┐
│ 3 matches · ◀ Previous  Next ▶          │
└──────────────────────────────────────────┘
```

- Matches are found by scanning all loaded messages for the query terms
- Each match is tagged with `data-search-hit` and an index
- "Next" scrolls to the next match (wraps around)
- "Previous" scrolls to the previous match (wraps around)
- The current match index (1 of N) is shown

The existing `MessageBubble` already supports content — we add an optional
`highlightTerms` prop and `data-search-hit` / `data-search-index`
attributes.

#### Files to create/modify

| File | Action |
|------|--------|
| `agent-home/src/lib/api/client.ts` | Add `searchSessions` method + types |
| `agent-home/src/app/api/chat/sessions/search/route.ts` | Create — BFF search route |
| `agent-home/src/components/chat/SessionSearchBar.tsx` | Create — search bar + dropdown |
| `agent-home/src/components/chat/SearchNav.tsx` | Create — next/previous nav bar |
| `agent-home/src/components/chat/MessageBubble.tsx` | Add `highlightTerms` prop for inline highlighting |
| `agent-home/src/components/chat/ChatPane.tsx` | Wire search state, pass query to MessageBubble |
| `agent-home/src/lib/chat/search.ts` | Create — search state + match-finding logic |

---

## Feature 5: In-Session Keyword Search with Next/Previous

### Goal

A search bar within the currently open session. Same next/previous
navigation UI as Feature 4, but scoped to the current session's loaded
messages only.

### Design

This reuses the same `SearchNav` component and `MessageBubble` highlighting
from Feature 4. The only difference is the entry point — a search icon in the
session detail area rather than the global search bar.

#### UI

A search icon button appears at the top-right of the thread container.
Tapping it expands an inline search bar:

```
┌──────────────────────────────────────────┐
│ 🔍 [type to search this chat____] [✕]   │
├──────────────────────────────────────────┤
│ 3 matches · ◀ Previous  Next ▶           │
├──────────────────────────────────────────┤
│ (message thread continues below)          │
```

#### Implementation

- `ChatPane` holds a `inSessionSearch` state (string | null)
- When non-null, all loaded messages are scanned for matches
- The same `SearchNav` component from Feature 4 renders
- `MessageBubble` receives `highlightTerms` and marks matching text
- Scrolling to matches uses `data-search-hit` + `scrollIntoView`

#### Files to create/modify

| File | Action |
|------|--------|
| `agent-home/src/components/chat/InSessionSearch.tsx` | Create — inline search bar + SearchNav |
| `agent-home/src/components/chat/ChatPane.tsx` | Wire in-session search state |
| `agent-home/src/components/chat/MessageBubble.tsx` | Shared with Feature 4 — highlightTerms |

---

## Shared Components

### `SearchNav` (used by Features 4 and 5)

```typescript
interface SearchNavProps {
  matchCount: number;
  currentIndex: number;  // 0-based
  onNext: () => void;
  onPrevious: () => void;
  onClose: () => void;
}
```

Renders a floating bar: `"{N} matches · ◀ Prev  Next ▶ · ✕"`

### `highlightTerms` in `MessageBubble`

Add an optional `highlightTerms?: string[]` prop. When provided, split the
message content into segments around each term occurrence and wrap matches
in `<mark>` tags. This mirrors the web dashboard's `SnippetHighlight`
pattern (which uses `>>>`/`<<<` delimiters from FTS5).

For agent-home, since we search client-side over loaded messages (not FTS5
snippets), we do simple `content.includes(term)` matching with case-
insensitive comparison.

### `TagChip` (used by Features 2 and 3)

```typescript
interface TagChipProps {
  tag: SessionTag;
  variant: 'display' | 'filter-include' | 'filter-exclude';
  onClick?: () => void;
  onRemove?: () => void;
}
```

---

## Implementation Order

1. **Feature 1** (context window in SessionModal) — smallest scope, no
   backend changes, just extend the TS type + add UI to an existing modal
2. **Feature 2** (tagging system) — backend-first (schema migration, API,
   BFF), then UI
3. **Feature 3** (tag filtering) — builds on Feature 2's tag tables
4. **Feature 5** (in-session search) — client-side only, builds on
   `MessageBubble` highlighting
5. **Feature 4** (cross-session search) — requires BFF route + search bar,
   reuses Feature 5's `SearchNav` and highlighting

Features 4 and 5 share the `SearchNav` component and `MessageBubble`
highlighting, so they should be implemented back-to-back.

---

## Schema Migration

New migration in `hermes_state.py` (schema v17 → v18):

```python
def _migrate_v17_to_v18(self, cursor):
    """v18: Session tags and tag-map tables."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_tags (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            color TEXT DEFAULT 'blue',
            created_at REAL NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_tag_map (
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            tag_id TEXT NOT NULL REFERENCES session_tags(id) ON DELETE CASCADE,
            source TEXT DEFAULT 'manual',
            suggested_at REAL,
            confirmed_at REAL,
            created_at REAL NOT NULL,
            PRIMARY KEY (session_id, tag_id)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_tag_map_tag "
        "ON session_tag_map(tag_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_tag_map_session "
        "ON session_tag_map(session_id)"
    )
    cursor.execute(
        "UPDATE schema_version SET version = 18"
    )
```

---

## Auto-Tagging Flow (detailed)

```
User sends message in ChatPane
       │
       ▼
  Turn completes (POST /api/sessions/{id}/chat)
       │
       ▼
  Has session ≥ 3 messages AND no tags?
       │ YES                    │ NO
       ▼                        ▼
  Background fetch:           Skip
  POST /api/sessions/{id}/tags/suggest
       │
       ▼
  Python API loads messages (last 10, capped ~4K tokens)
  + existing tag names
       │
       ▼
  DeepSeek call: "classify this conversation"
       │
       ▼
  Returns {suggested_tags, new_tags}
       │
       ▼
  Store as session_tag_map rows with source='auto-suggested'
       │
       ▼
  User opens SessionModal next time
       │
       ▼
  Shows suggestion card: "💡 refactoring — [Accept] [Dismiss]"
       │
  Accept → source='auto-confirmed', confirmed_at=now
  Dismiss → DELETE from session_tag_map
```

The auto-tagging endpoint uses DeepSeek (same as triage agents) with
`response_format: {'type': 'json_object'}` for structured output.

---

## Test Plan

### Python-side tests

- `tests/test_session_tags.py` — tag CRUD, session-tag mapping, tag-filter
  SQL (AND/OR/NOT), auto-tag suggestion endpoint with mocked DeepSeek
- Extend `tests/test_hermes_state.py` — v18 migration, tag tables exist,
  cascade delete works

### Agent-home tests

- `agent-home/src/components/chat/SessionModal.test.tsx` — context window
  section renders, collapse button works, tag section renders
- `agent-home/src/components/chat/TagFilterBar.test.tsx` — AND/OR/NOT
  filter state
- `agent-home/src/components/chat/SessionSearchBar.test.tsx` — debounced
  search, results render
- `agent-home/src/components/chat/SearchNav.test.tsx` — next/previous
  navigation
- `agent-home/src/lib/chat/search.test.ts` — match-finding logic

### E2E verification

- Create a session → send 3 messages → auto-tag suggestion appears in
  SessionModal → accept → tag shows on session chip
- Add 2 tags to 3 sessions → filter by AND → only sessions with both tags
  show → filter by NOT → sessions without that tag show
- Search "deploy" → results show matching sessions → click one → matches
  highlighted → next/previous navigates between them
- Open in-session search → type "ecs" → matches highlighted →
  next/previous navigates

---

## Assumptions

1. **Tags are per-profile** — stored in the same `state.db` as sessions,
   following the existing profile isolation model
2. **Context window shows persisted token sums** — for historical sessions,
   we show `input_tokens + output_tokens + cache_tokens` from the sessions
   table. Live context breakdown (system prompt + tools + conversation) is
   only available for the active session and requires a live agent process.
3. **Auto-tagging uses DeepSeek** — consistent with the triage agent pattern
   already deployed on the ECS box
4. **Cross-session search uses the existing FTS5 endpoint** — no new search
   infrastructure needed; the Python API's `/api/sessions/search` already
   handles lineage dedup and snippet generation
5. **In-session search is client-side** — searches through loaded messages
   only, no server round-trip needed
