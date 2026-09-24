"use client";

import { useMemo } from "react";

import { useRowStream } from "@/components/projects/useRowStream";
import type { StreamFrame } from "@/lib/chat/stream";
import type { ProjectCardDetail } from "@/types";

/** Only `running` has anything to watch for: a worker is on it right now,
 * and its heartbeat note / last comment can change between frames. Every
 * other status is either not-yet-started or already closed — a reload
 * (or the run page's own live view) is how those move. */
export function isCardLive(status: string): boolean {
  return status === "running";
}

/**
 * Fold one frame from `GET .../cards/:id/stream` into a fresh card row, or
 * `null` for a frame this hook doesn't act on (`gone`/`end` carry no row —
 * `useRowStream` already stops the connection for those). Split out so the
 * contract is testable without a DOM — mirrors `applyRunFrame`
 * (`useRunLive.ts`) exactly.
 */
export function applyCardFrame(frame: StreamFrame): ProjectCardDetail | null {
  if (frame.event !== "update") return null;
  if (typeof frame.data.status !== "string") return null;
  return frame.data as unknown as ProjectCardDetail;
}

/**
 * Keep one card row current while a worker is actually on it — pushed by
 * the server (§12 push edition) instead of polled on a timer. Stops for
 * good the moment the card leaves `running` and does not reconnect —
 * reopening the page (or the status changing again) re-evaluates.
 */
export function useCardLive(
  slug: string,
  taskId: string,
  status: string,
  onCard: (card: ProjectCardDetail) => void,
): void {
  const path = useMemo(
    () =>
      isCardLive(status)
        ? `/api/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(taskId)}/stream`
        : null,
    [slug, taskId, status],
  );
  useRowStream(path, (frame) => {
    const card = applyCardFrame(frame);
    if (card) onCard(card);
  });
}
