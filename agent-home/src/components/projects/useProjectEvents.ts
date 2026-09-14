"use client";

import { useRouter } from "next/navigation";
import { useRef } from "react";

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
  useRowStream(
    `/api/projects/${encodeURIComponent(slug)}/events/stream`,
    (frame) => {
      const result = applyProjectEventsFrame(seenRef.current, frame);
      seenRef.current = result.seen;
      if (result.moved) router.refresh();
    },
  );
}
