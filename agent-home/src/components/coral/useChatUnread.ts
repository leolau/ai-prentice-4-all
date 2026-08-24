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
import type { SessionSummary } from "@/types";

const POLL_MS = 45_000;

export function useChatUnreadCount(): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const res = await fetch("/api/chat/sessions?limit=200", {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as { sessions?: SessionSummary[] };
        if (!cancelled) setCount(countUnreadSessions(data.sessions ?? []));
      } catch {
        // Unreachable backend — the badge keeps its last value.
      }
    }

    void refresh();
    const interval = window.setInterval(() => void refresh(), POLL_MS);
    const onWake = () => void refresh();
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
