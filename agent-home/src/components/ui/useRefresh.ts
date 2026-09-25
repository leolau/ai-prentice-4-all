"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState, useTransition } from "react";

/**
 * `router.refresh()` that reports when it has *landed*.
 *
 * A bare `router.refresh()` returns immediately; the new server render
 * arrives some time later (seconds, on a busy box). Views that cleared their
 * busy flag in `finally` therefore re-enabled their buttons while the page
 * still showed the pre-action state — which reads as "nothing happened" and
 * invites a second click. Wrapping the refresh in a transition keeps
 * `refreshing` true until the fresh tree has rendered, so callers can hold
 * the busy overlay (and `disabled`) across the whole round trip.
 */
export function useRefresh(): { refresh: () => void; refreshing: boolean } {
  const router = useRouter();
  const [refreshing, startTransition] = useTransition();
  const refresh = useCallback(() => {
    startTransition(() => router.refresh());
  }, [router]);
  return { refresh, refreshing };
}

/**
 * Local, editable copy of a server-rendered prop that follows the prop.
 *
 * `useState(initial)` alone snapshots the prop on mount and never looks at
 * it again, so after `router.refresh()` the server sends a new row but the
 * view keeps showing the old one until the user leaves and comes back.
 * This resets the copy whenever the server hands down a new object (the
 * prop's identity changes on every server render), while still letting the
 * view patch it locally in between (live streams, optimistic updates).
 */
export function useServerState<T>(initial: T): [T, (update: T | ((prev: T) => T)) => void] {
  const [state, setState] = useState(initial);
  const [seen, setSeen] = useState(initial);
  if (initial !== seen) {
    setSeen(initial);
    setState(initial);
    return [initial, setState];
  }
  return [state, setState];
}
