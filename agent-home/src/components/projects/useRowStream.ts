"use client";

import { useEffect, useRef } from "react";

import { readSseFrames, type StreamFrame } from "@/lib/chat/stream";

/**
 * Reconnect backoff after an unexpected stream drop (a network blip, a
 * proxy's idle timeout, a backend restart) — capped, so a stream that keeps
 * failing doesn't hammer the server. Resets to the first step the moment a
 * connection succeeds.
 */
export const RECONNECT_DELAYS_MS = [500, 1_000, 2_000, 5_000, 10_000];

export interface RowStreamHandle {
  /** Tear the connection down for good — no further reconnect attempt. */
  stop(): void;
}

/**
 * Drive one row-level live stream's connect/reconnect loop. Split out of
 * the hook so the algorithm is testable without a DOM: opens `path`, hands
 * every frame to `onFrame`, and reconnects with capped backoff on an
 * unexpected drop. Stops for good once a frame reports `end` or `gone`
 * (the server's own signal that there is nothing left to watch), or
 * `stop()` is called.
 */
export function openRowStream(
  path: string,
  onFrame: (frame: StreamFrame) => void,
  fetchImpl: typeof fetch = fetch,
  scheduleRetry: (run: () => void, delayMs: number) => () => void = (run, ms) => {
    const id = setTimeout(run, ms);
    return () => clearTimeout(id);
  },
): RowStreamHandle {
  let stopped = false;
  let attempt = 0;
  let cancelRetry: (() => void) | undefined;
  let controller: AbortController | undefined;

  const connect = () => {
    if (stopped) return;
    controller = new AbortController();
    void (async () => {
      let closedByServer = false;
      try {
        const res = await fetchImpl(path, { signal: controller!.signal });
        if (!res.ok || !res.body) throw new Error("stream unavailable");
        attempt = 0; // a successful connect resets the backoff
        await readSseFrames(res, (frame) => {
          if (stopped) return;
          if (frame.event === "end" || frame.event === "gone") {
            closedByServer = true;
          }
          onFrame(frame);
        });
      } catch {
        // A network failure or an aborted fetch (unmount, or `path`
        // changing) — either way, fall through to the same decision below
        // rather than surfacing anything: a dropped live view is not an
        // error a person can act on.
      }
      if (stopped || closedByServer) return;
      // The connection ended without the server saying it was done — an
      // ordinary hiccup, not a terminal signal. Reconnect.
      const delay =
        RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)];
      attempt += 1;
      cancelRetry = scheduleRetry(connect, delay);
    })();
  };

  connect();
  return {
    stop() {
      stopped = true;
      controller?.abort();
      cancelRetry?.();
    },
  };
}

/**
 * Open one of the row-level live streams (run / card / project events) and
 * hand every frame to `onFrame`, for as long as `path` is non-null.
 * `path` turning `null` — e.g. a run reaching its terminal status — tears
 * the connection down and does not reopen it until `path` is set again.
 *
 * Shared by `useRunLive`, `useCardLive` and `useProjectEvents` (§12 push
 * edition) so the reconnect behaviour — the part a person never sees
 * directly but very much feels if it's wrong — lives in exactly one place.
 */
export function useRowStream(
  path: string | null,
  onFrame: (frame: StreamFrame) => void,
): void {
  const onFrameRef = useRef(onFrame);
  useEffect(() => {
    onFrameRef.current = onFrame;
  }, [onFrame]);

  useEffect(() => {
    if (!path) return;
    const handle = openRowStream(path, (frame) => onFrameRef.current(frame));
    return () => handle.stop();
  }, [path]);
}
