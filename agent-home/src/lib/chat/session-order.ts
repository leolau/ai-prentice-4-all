/**
 * Client-side persistence for the user's manual ordering of the session tabs.
 *
 * The server returns conversations in recency order; the user can drag the top
 * strip into a preferred sequence. That sequence is a list of session ids kept
 * in `localStorage` (device-local, like the theme choice) and re-applied every
 * time the session list is (re)loaded from the server. It is stored as a JSON
 * string so it can back a `usePersistentState` string value (a stable snapshot
 * for `useSyncExternalStore`); the array form is derived with `parseOrder`.
 */
export const SESSION_ORDER_STORAGE_KEY = "agent-home:session-order";

/** Parse the stored JSON string into a list of ids (empty on any problem). */
export function parseOrder(raw: string): string[] {
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((x): x is string => typeof x === "string")
      : [];
  } catch {
    return [];
  }
}

/**
 * Return `sessions` sorted by the saved `order`. Sessions present in `order`
 * follow that sequence; any not in it (e.g. a brand-new conversation) keep
 * their incoming relative order and are placed first, so new work stays
 * visible at the front of the strip.
 */
export function orderSessions<T extends { id: string }>(
  sessions: T[],
  order: string[],
): T[] {
  const rank = new Map(order.map((id, i) => [id, i]));
  return sessions
    .map((s, i) => ({ s, i }))
    .sort((a, b) => {
      const ra = rank.has(a.s.id) ? (rank.get(a.s.id) as number) : -1;
      const rb = rank.has(b.s.id) ? (rank.get(b.s.id) as number) : -1;
      if (ra !== rb) return ra - rb;
      return a.i - b.i; // stable within the same rank
    })
    .map((x) => x.s);
}

/**
 * Pick the conversation to open after `archivedId` is archived out of the
 * displayed strip. Returns the next remaining session's id in display order
 * (preferring the one after the archived tab, else the one before), or `null`
 * when nothing remains — the caller then falls back to the empty state.
 *
 * Archiving the open conversation must switch to a neighbouring conversation,
 * never drop the user into a fresh "New conversation".
 */
export function nextActiveAfterArchive(
  orderedIds: string[],
  archivedId: string,
): string | null {
  const remaining = orderedIds.filter((id) => id !== archivedId);
  if (remaining.length === 0) return null;
  const idx = orderedIds.indexOf(archivedId);
  if (idx === -1) return remaining[0];
  // First still-present id at or after the archived slot, else the last one
  // before it.
  for (let i = idx + 1; i < orderedIds.length; i += 1) {
    if (orderedIds[i] !== archivedId) return orderedIds[i];
  }
  for (let i = idx - 1; i >= 0; i -= 1) {
    if (orderedIds[i] !== archivedId) return orderedIds[i];
  }
  return remaining[0];
}
