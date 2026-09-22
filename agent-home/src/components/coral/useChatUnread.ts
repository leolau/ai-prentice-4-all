"use client";

/**
 * Unread-topic count for the Chats tile. Last-read markers live in the
 * browser, so this runs client-side: poll the sessions list (cheap, cached
 * server-side) and compare each session's `last_active` against what this
 * device has read. A delivery into a topic bumps `last_active` and the badge
 * appears on the next tick or refocus.
 */
import { useEffect, useState } from "react";

import { countUnreadSessions } from "@/lib/chat/last-read";
import { fetchSessionList } from "@/lib/chat/session-list-fetch";

const POLL_MS = 45_000;

export function useChatUnreadCount(): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      // Coalesced: focus + visibilitychange fire together on a resume, and a
      // timer tick or a ChatPane refresh may already have one in flight —
      // all of those join the same request instead of duplicating it.
      const data = await fetchSessionList("/api/chat/sessions?limit=200");
      if (data && !cancelled) {
        setCount(countUnreadSessions(data.sessions ?? []));
      }
    }

    void refresh();
    const interval = window.setInterval(() => void refresh(), POLL_MS);
    // A resume fires BOTH focus and visibilitychange in the same tick (the
    // coalescer folds them into one request); a hide fires visibilitychange
    // alone, where polling is pointless.
    const onWake = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", onWake);
    document.addEventListener("visibilitychange", onWake);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", onWake);
      document.removeEventListener("visibilitychange", onWake);
    };
  }, []);

  return count;
}
