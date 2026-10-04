/**
 * The chat strip's session fetch size — 200 is the BFF route's own hard
 * cap (`Math.min(200, ...)` in `api/chat/sessions/route.ts`), i.e. this is
 * "as many as the backend will give us in one page".
 *
 * This used to be lowered to 50 for a faster first paint, on the theory
 * that a strip only shows a handful of tabs at once — but that meant any
 * conversation outside the 50 most recent silently vanished from the
 * picker entirely (found via a real report: a session ranked #65 by
 * recency was invisible). The `ConversationList` sidebar is its own
 * scroll container filtered by category (see `categorize.ts`), so a wider
 * fetch no longer means a taller page — only the list grows, and that's
 * exactly what "load more of this category" should do. If a
 * future perf issue reappears, the fix belongs in the query
 * (`hermes_state.py`'s `list_sessions_rich`), not in silently hiding
 * conversations again.
 *
 * `refreshSessions()` in `ChatPane` must use the same value as the initial
 * server-side fetch in `chat/page.tsx` — a refresh returning fewer rows than
 * the first paint would visibly shrink the strip.
 */
export const CHAT_SESSION_LIST_LIMIT = 200;
