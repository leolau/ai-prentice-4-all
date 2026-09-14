"use client";

import { useMemo } from "react";

import { useRowStream } from "@/components/projects/useRowStream";
import type { StreamFrame } from "@/lib/chat/stream";
import type { ProjectRun } from "@/types";

/** Statuses at which a run stops moving — nothing left to watch. */
const TERMINAL = new Set(["done", "failed", "cancelled"]);

/**
 * A terminal run row can still have a card actively working: the stale-run
 * sweep (Gap B) can fail a run while a card is only rate-limited, not truly
 * dead, and a human can act on that card directly (`hermes kanban unblock`,
 * or the new Resume action) without the run's own row ever reopening in the
 * same instant. Without this, the page would freeze on "Failed" the moment
 * the row closed and never show the card actually finishing — exactly the
 * confusion a real run hit in production (see
 * plans/2026-09-13-project-run-resilience-plan.md). So a terminal row is
 * still "live" for streaming purposes as long as something on it is running.
 */
export function isRunLive(status: string, hasActiveCard = false): boolean {
  if (!TERMINAL.has(status)) return true;
  return hasActiveCard;
}

/**
 * Fold one frame from `GET .../runs/:n/stream` into a fresh run row, or
 * `null` for a frame this hook doesn't act on (`gone`/`end` carry no row —
 * `useRowStream` already stops the connection for those). Split out so the
 * contract is testable without a DOM.
 */
export function applyRunFrame(frame: StreamFrame): ProjectRun | null {
  if (frame.event !== "update") return null;
  if (typeof frame.data.status !== "string") return null;
  return frame.data as unknown as ProjectRun;
}

/**
 * Keep one run row current while it is still moving — pushed by the
 * server (§12 push edition) instead of polled on a timer. The server
 * re-derives the cards' board state, the blocked set, `stalled`, cost and
 * duration on every change and sends the whole row again, so applying a
 * frame IS the live update — no second endpoint and no client-side state
 * machine to disagree with the server.
 *
 * Stops for good at the run's terminal status (unless a card is still
 * active — see `isRunLive`) and does not reconnect, which is what keeps a
 * long-open finished run page from holding a connection open all day.
 */
export function useRunLive(
  slug: string,
  runNo: number,
  status: string,
  onRun: (run: ProjectRun) => void,
  /**
   * Whether a card on this run is currently `running`, as of the caller's
   * last-known state. Keeps the stream open across a terminal row with a
   * card still working (see `isRunLive`'s doc) — omit for a run with no
   * cards or when the caller doesn't track this.
   */
  hasActiveCard = false,
): void {
  const path = useMemo(
    () =>
      isRunLive(status, hasActiveCard)
        ? `/api/projects/${encodeURIComponent(slug)}/runs/${runNo}/stream`
        : null,
    [slug, runNo, status, hasActiveCard],
  );
  useRowStream(path, (frame) => {
    const run = applyRunFrame(frame);
    if (run) onRun(run);
  });
}
