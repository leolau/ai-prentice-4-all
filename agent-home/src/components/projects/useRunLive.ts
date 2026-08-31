"use client";

import { useEffect, useRef } from "react";

import type { ProjectRun } from "@/types";

/**
 * A live run's re-read interval. Faster than the project page's 15s event
 * tail because this is the surface a person watches while work happens.
 */
export const RUN_POLL_INTERVAL_MS = 5_000;

/** Statuses at which a run stops moving — polling has nothing left to see. */
const TERMINAL = new Set(["done", "failed", "cancelled"]);

export function isRunLive(status: string): boolean {
  return !TERMINAL.has(status);
}

/**
 * One re-read of `GET /api/projects/:slug/runs/:n`, split out of the hook so
 * the contract is testable without a DOM:
 *
 * - it reads only while the tab is visible — a backgrounded phone must not
 *   keep a run's worth of requests going;
 * - it swallows every failure, network or non-2xx, because a live update is
 *   an optimisation and a failed poll is not an error a person can act on;
 * - it hands back whether the run is still live, so the caller can stop
 *   polling the moment the run reaches a terminal status rather than
 *   re-reading a finished row forever.
 */
export function createRunPoller(
  slug: string,
  runNo: number,
  onRun: (run: ProjectRun) => void,
  fetchImpl: typeof fetch = fetch,
): { tick: () => Promise<boolean> } {
  const tick = async (): Promise<boolean> => {
    if (
      typeof document !== "undefined" &&
      document.visibilityState !== "visible"
    ) {
      return true; // hidden is not finished — keep the timer armed
    }
    try {
      const res = await fetchImpl(
        `/api/projects/${encodeURIComponent(slug)}/runs/${runNo}`,
      );
      if (!res.ok) return true;
      const data = (await res.json().catch(() => null)) as ProjectRun | null;
      if (data == null || typeof data.status !== "string") return true;
      onRun(data);
      return isRunLive(data.status);
    } catch {
      return true;
    }
  };
  return { tick };
}

/**
 * Keep one run row current while it is still moving. The server re-derives
 * the cards' board state, the blocked set, `stalled`, cost and duration on
 * every read, so re-reading the row IS the live update — no second endpoint
 * and no client-side state machine to disagree with the server.
 *
 * Polling stops at the run's terminal status and does not restart, which is
 * what keeps a long-open finished run page from talking to the box all day.
 */
export function useRunLive(
  slug: string,
  runNo: number,
  status: string,
  onRun: (run: ProjectRun) => void,
): void {
  // The callback changes identity on every render; hold it in a ref so the
  // effect depends on the run's identity and status, not on the closure.
  const onRunRef = useRef(onRun);
  useEffect(() => {
    onRunRef.current = onRun;
  }, [onRun]);

  useEffect(() => {
    if (!isRunLive(status)) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poller = createRunPoller(slug, runNo, (run) =>
      onRunRef.current(run),
    );
    const loop = async () => {
      const live = await poller.tick();
      if (stopped || !live) return;
      timer = setTimeout(() => void loop(), RUN_POLL_INTERVAL_MS);
    };
    timer = setTimeout(() => void loop(), RUN_POLL_INTERVAL_MS);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [slug, runNo, status]);
}
