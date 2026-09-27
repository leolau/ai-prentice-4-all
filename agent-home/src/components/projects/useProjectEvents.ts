"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { useRowStream } from "@/components/projects/useRowStream";
import type { StreamFrame } from "@/lib/chat/stream";

/**
 * Fold one frame from `GET .../events/stream` into the cursor's next value.
 * `moved` is true only when a head was already known and increased — the
 * very first frame just seeds `seen`, because a page's initial server
 * render already reflects that head, and refreshing on it would be a
 * pointless round trip rather than a real update. Split out so the
 * contract is testable without a DOM.
 */
export function applyProjectEventsFrame(
  seen: number | null,
  frame: StreamFrame,
): { seen: number | null; moved: boolean } {
  if (frame.event !== "update") return { seen, moved: false };
  const head = frame.data.latest_event_id;
  if (typeof head !== "number") return { seen, moved: false };
  if (seen === null) return { seen: head, moved: false };
  if (head > seen) return { seen: head, moved: true };
  return { seen, moved: false };
}

/**
 * Minimum spacing between whole-page `router.refresh()` calls the events
 * stream is allowed to trigger. A busy run can emit several event frames a
 * second and every refresh re-renders the full detail page — a fan-out of
 * board/playbook/directives/doctor/runs fetches. Throttling folds a burst
 * into at most one refresh per interval, with a trailing call so the last
 * event in a burst still lands.
 */
export const PROJECT_REFRESH_MIN_INTERVAL_MS = 2_000;

/**
 * The live-update tail for one project, pushed by the server (§12 push
 * edition) instead of polled on a timer: calls `router.refresh()` whenever
 * the project's event cursor moves. The server re-derives progress, health
 * and the rollup on read, so a refresh IS the update — the hook is the
 * whole feature. Never stops on its own (a project has no terminal
 * state) — only unmounting tears the connection down.
 */
export function useProjectEvents(slug: string): void {
  const router = useRouter();
  const seenRef = useRef<number | null>(null);
  const lastRefreshRef = useRef(0);
  const timerRef = useRef<number | null>(null);

  // Drop any pending trailing refresh on unmount.
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  useRowStream(
    `/api/projects/${encodeURIComponent(slug)}/events/stream`,
    (frame) => {
      const result = applyProjectEventsFrame(seenRef.current, frame);
      seenRef.current = result.seen;
      if (!result.moved) return;
      const now = Date.now();
      const elapsed = now - lastRefreshRef.current;
      if (elapsed >= PROJECT_REFRESH_MIN_INTERVAL_MS) {
        lastRefreshRef.current = now;
        router.refresh();
      } else if (timerRef.current === null) {
        // Inside the throttle window: schedule the trailing refresh so a
        // burst still lands its final state once the window opens.
        timerRef.current = window.setTimeout(() => {
          timerRef.current = null;
          lastRefreshRef.current = Date.now();
          router.refresh();
        }, PROJECT_REFRESH_MIN_INTERVAL_MS - elapsed);
      }
    },
  );
}
