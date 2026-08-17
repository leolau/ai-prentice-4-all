"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** The detail page's poll interval (§12 live updates). */
const POLL_INTERVAL_MS = 15_000;

/**
 * One cursor-aware poll cycle against `GET /api/projects/:slug/events` (E3).
 * Split out of the hook so the contract is testable without a DOM: the
 * cursor is seeded from the *first* response (a 0 would refresh for history
 * the page already rendered), a movement calls `onMovement` exactly once per
 * head change, and every failure — network or non-2xx — is swallowed,
 * because live updates are an optimisation, never a surfaced error.
 */
export function createProjectEventsPoller(
  slug: string,
  onMovement: () => void,
  fetchImpl: typeof fetch = fetch,
): { tick: () => Promise<void> } {
  let since: number | null = null;
  const tick = async () => {
    // Poll only while the tab is visible; under a DOM-less test runner the
    // check simply passes.
    if (
      typeof document !== "undefined" &&
      document.visibilityState !== "visible"
    ) {
      return;
    }
    try {
      const query = since != null ? `?since=${since}` : "";
      const res = await fetchImpl(
        `/api/projects/${encodeURIComponent(slug)}/events${query}`,
      );
      if (!res.ok) return; // never surface a poll error
      const data = (await res.json().catch(() => null)) as
        | { latest_event_id?: unknown }
        | null;
      const head =
        data != null && typeof data.latest_event_id === "number"
          ? data.latest_event_id
          : null;
      if (head == null) return;
      if (since === null) {
        since = head; // seed from the first answer, not 0
        return;
      }
      if (head > since) {
        since = head; // the head, not the last event
        onMovement();
      }
    } catch {
      // a network failure is swallowed too — same contract
    }
  };
  return { tick };
}

/**
 * The live-update tail for one project: polls while mounted and calls
 * `router.refresh()` when the event head moves. The server re-derives
 * progress, health and the rollup on read, so a refresh IS the update —
 * the hook is the whole feature.
 */
export function useProjectEvents(slug: string): void {
  const router = useRouter();
  useEffect(() => {
    const poller = createProjectEventsPoller(slug, () => router.refresh());
    void poller.tick();
    const timer = setInterval(() => void poller.tick(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [slug, router]);
}
