/**
 * Per-session "last read" markers for the Chats unread badge. The state is
 * inherently browser-local (what THIS device has seen), so it lives in
 * localStorage; the count is computed against each session's `last_active`
 * (unix seconds) from the sessions list.
 */
import type { SessionSummary } from "@/types";

const STORAGE_KEY = "agent-home:last-read";

export type LastReadMap = Record<string, number>;

export function readLastReadMap(): LastReadMap {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as LastReadMap) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

/** Record that the user just looked at this session. */
export function markSessionRead(sessionId: string): void {
  if (!sessionId) return;
  try {
    const map = readLastReadMap();
    map[sessionId] = Date.now() / 1000;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Storage full/blocked — the badge just stays conservative.
  }
}

/** Sessions whose latest activity is newer than what this device has read. */
export function countUnreadSessions(
  sessions: Pick<SessionSummary, "id" | "last_active" | "archived">[],
  map: LastReadMap = readLastReadMap(),
): number {
  let count = 0;
  for (const session of sessions) {
    if (session.archived) continue;
    const lastActive = session.last_active;
    if (lastActive == null) continue;
    const read = map[session.id];
    if (read == null || lastActive > read) count += 1;
  }
  return count;
}
