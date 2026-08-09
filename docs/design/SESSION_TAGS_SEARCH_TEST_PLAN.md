# Test Plan: Session Tags, Search & Context Window Features

**Scope:** Tests for the five session management features implemented in PR #155
(branch `feat/session-tags-search-context`).

**Test layers:**
1. **Python backend** — `SessionDB` tag CRUD + filtering (real SQLite, `tmp_path`)
2. **Python API** — `web_server.py` tag endpoints + tag filter on `GET /api/sessions`
3. **TypeScript BFF routes** — agent-home Next.js API routes proxying to Python
4. **TypeScript API client** — `HermesApiClient` tag/search method forwarding
5. **TypeScript components** — `SessionModal`, `TagFilterBar`, `SearchNav`,
   `MessageBubble` highlight, `SessionSearchBar`, `InSessionSearch`

---

## 1. Python Backend: `tests/test_session_tags.py`

Real `SessionDB` under `tmp_path/state.db`; no HTTP, no mocks.

| Test | Description |
|---|---|
| `test_add_tag_creates_tag_and_mapping` | `add_tag_to_session` creates a row in `session_tags` and `session_tag_map`; returns `{id, name, color}` |
| `test_add_tag_idempotent` | Adding the same tag name twice to the same session does not duplicate |
| `test_add_tag_case_insensitive` | `"Bug"` then `"bug"` resolves to the same tag row |
| `test_add_tag_invalid_color_falls_back` | `color="not-a-color"` falls back to `"blue"` |
| `test_get_session_tags_returns_attached` | After adding 3 tags, `get_session_tags` returns all 3 ordered by name |
| `test_get_session_tags_empty` | Session with no tags returns `[]` |
| `test_remove_tag_from_session` | After removal, `get_session_tags` no longer includes it |
| `test_remove_tag_not_attached` | Removing a tag that isn't attached returns `False` |
| `test_delete_tag_cascade` | `delete_tag` removes the tag row and all `session_tag_map` rows for it |
| `test_list_tags_with_count` | `list_tags` returns all tags with `session_count` |
| `test_filter_include_any` | `filter_session_ids_by_tags(include=["a","b"], match="any")` returns sessions with either tag (OR) |
| `test_filter_include_all` | `match="all"` returns only sessions that have every requested tag (AND) |
| `test_filter_exclude_only` | Only `exclude_tags` set → starts from all sessions, removes those with the tag |
| `test_filter_include_plus_exclude` | Include AND exclude together |
| `test_filter_no_tags_returns_none` | Empty include + empty exclude → `None` (caller should not filter) |
| `test_schema_migration_v18` | Opening a DB created at v17 and then at v18 creates the tag tables |

## 2. Python API: `tests/hermes_cli/test_web_server_tags.py`

`TestClient(app)` with mocked `_comms_resolve_principal`; real `SessionDB`
under `tmp_path/HERMES_HOME`.

| Test | Description |
|---|---|
| `test_list_tags_empty` | `GET /api/sessions/tags` returns `{"tags": []}` |
| `test_list_tags_with_entries` | After adding tags, returns them with counts |
| `test_add_tag_endpoint` | `POST /api/sessions/{id}/tags` with `{"name":"bug"}` returns `{"tag": {...}}` |
| `test_add_tag_missing_name` | 400 when body has no `name` |
| `test_get_session_tags` | `GET /api/sessions/{id}/tags` returns attached tags |
| `test_remove_tag_endpoint` | `DELETE /api/sessions/{id}/tags/{tag_id}` returns `{"ok": true}` |
| `test_delete_tag_endpoint` | `DELETE /api/sessions/tags/{tag_id}` removes the tag |
| `test_sessions_tag_filter` | `GET /api/sessions?tags=bug` filters the session list |
| `test_sessions_tag_filter_exclude` | `GET /api/sessions?exclude_tags=bug` excludes matching sessions |
| `test_sessions_tag_filter_and` | `GET /api/sessions?tags=a,b&tag_match=all` (AND) |
| `test_suggest_tags_returns_suggestions` | `POST /api/sessions/{id}/tags/suggest` returns `{"suggestions": [...]}` (LLM stubbed) |

## 3. TypeScript BFF Routes

Mocked `apiClientForRequest`; verify the BFF forwards to the correct Python
API path, method, and body.

### `tags/route.test.ts`

| Test | Description |
|---|---|
| `returns 401 when unauthenticated` | No principal → 401 |
| `forwards to client.listTags` | Returns `{"tags": [...]}` |

### `tags/add/route.test.ts`

| Test | Description |
|---|---|
| `returns 401 when unauthenticated` | |
| `400 on missing sessionId` | |
| `400 on missing name` | |
| `forwards name + color to addSessionTag` | |

### `tags/remove/route.test.ts`

| Test | Description |
|---|---|
| `returns 401 when unauthenticated` | |
| `400 on missing params` | |
| `forwards to removeSessionTag` | |

### `search/route.test.ts`

| Test | Description |
|---|---|
| `returns 401 when unauthenticated` | |
| `returns empty results for empty query` | |
| `forwards q to client.searchSessions` | |

## 4. TypeScript API Client: `client.tags.test.ts`

Mocked `globalThis.fetch`; verify URL, method, headers, body.

| Test | Description |
|---|---|
| `listTags GETs /api/sessions/tags` | |
| `getSessionTags GETs encoded path` | |
| `addSessionTag POSTs name + color` | |
| `removeSessionTag DELETEs encoded path` | |
| `deleteTag DELETEs encoded tag path` | |
| `suggestSessionTags POSTs to suggest` | |
| `searchSessions GETs search with q param` | |
| `sessions forwards tag filter params` | tags, exclude_tags, tag_match appear in URL |

## 5. TypeScript Components

### `SearchNav.test.tsx`

| Test | Description |
|---|---|
| `renders current/total match count` | `2 / 5` when current=1, total=5 |
| `renders "No matches" when total is 0` | |
| `disables arrows when total is 0` | |

### `TagFilterBar.test.tsx`

| Test | Description |
|---|---|
| `renders nothing when no tags` | |
| `renders tag chips` | |
| `includes tag highlighted when in includeTags` | |
| `excludes tag struck-through when in excludeTags` | |

### `SessionModal.test.tsx`

| Test | Description |
|---|---|
| `renders context window section with token total` | |
| `collapsible: hidden when collapsed` | |
| `renders tag chips when tags provided` | |
| `renders tag input when onAddTag provided` | |
| `renders suggestions with Accept/Dismiss` | |

### `MessageBubble` highlight test (extend existing)

| Test | Description |
|---|---|
| `renders data-msg-index` | |
| `wraps search term in <mark> for user content` | |
