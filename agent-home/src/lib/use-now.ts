"use client";

import { useEffect, useState } from "react";

/**
 * The current time (`Date.now()`), refreshed every `intervalMs` while
 * `active`. Only the calling component re-renders on each tick, so a live
 * clock no longer forces its whole parent tree to re-render once a second.
 */
export function useNow(active: boolean, intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const tick = () => setNow(Date.now());
    // Refresh straight away: `now` may be stale from a previous active spell.
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, intervalMs);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [active, intervalMs]);
  return now;
}
