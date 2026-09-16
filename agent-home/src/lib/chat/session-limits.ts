/**
 * The chat strip's first-paint session fetch size. `order: "recent"` drives
 * an expensive recency query (a recursive compression-chain walk — see
 * `hermes_state.py`'s `list_sessions_rich`); fetching 200 rows on every page
 * load and every background refresh was pure waste for a strip that only
 * shows a handful of tabs at once. The full history is reachable via the
 * "All conversations" sheet, which fetches its own wider set on demand.
 *
 * `refreshSessions()` in `ChatPane` must use the same value as the initial
 * server-side fetch in `chat/page.tsx` — a refresh returning fewer rows than
 * the first paint would visibly shrink the strip.
 */
export const CHAT_SESSION_LIST_LIMIT = 50;
