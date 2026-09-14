/**
 * `openRowStream`'s connect/reconnect contract (§12 live updates, push
 * edition) — the algorithm behind `useRunLive` / `useCardLive` /
 * `useProjectEvents`, tested without a DOM or real timers: a scriptable
 * `scheduleRetry` stands in for `setTimeout` so a reconnect fires
 * immediately instead of after a real delay.
 */
import { describe, expect, it, vi } from "vitest";

import {
  RECONNECT_DELAYS_MS,
  openRowStream,
} from "@/components/projects/useRowStream";
import type { StreamFrame } from "@/lib/chat/stream";

/** An SSE body a real `fetch` would return, readable by `readSseFrames`. */
function sseResponse(blocks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const block of blocks) controller.enqueue(encoder.encode(block));
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

function frame(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** A `scheduleRetry` that runs the retry synchronously and records the
 * delay it was asked for, instead of waiting on a real timer. */
function immediateRetries(): {
  scheduleRetry: (run: () => void, delayMs: number) => () => void;
  delays: number[];
} {
  const delays: number[] = [];
  return {
    delays,
    scheduleRetry: (run, delayMs) => {
      delays.push(delayMs);
      run();
      return () => {};
    },
  };
}

describe("openRowStream", () => {
  it("delivers every frame and never reconnects once the server says end", async () => {
    const fetchImpl = vi.fn(async () =>
      sseResponse([frame("update", { n: 1 }), frame("end", {})]),
    );
    const { scheduleRetry, delays } = immediateRetries();
    const received: StreamFrame[] = [];
    openRowStream("/x", (f) => received.push(f), fetchImpl, scheduleRetry);
    await vi.waitFor(() => expect(received.map((f) => f.event)).toEqual(["update", "end"]));

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(delays).toEqual([]); // an `end` frame is a terminal signal, not a drop
  });

  it("reconnects with capped, increasing backoff on repeated connection failures", async () => {
    let call = 0;
    const fetchImpl = vi.fn(async () => {
      call += 1;
      if (call <= 3) return new Response(null, { status: 502 }); // fails to even open
      return sseResponse([frame("update", { n: call }), frame("end", {})]);
    });
    const { scheduleRetry, delays } = immediateRetries();
    const received: StreamFrame[] = [];
    openRowStream("/x", (f) => received.push(f), fetchImpl, scheduleRetry);
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(4));

    expect(delays).toEqual(RECONNECT_DELAYS_MS.slice(0, 3));
    expect(received.map((f) => f.event)).toEqual(["update", "end"]);
  });

  it("resets the backoff the moment a connection opens, even if it then drops", async () => {
    let call = 0;
    const fetchImpl = vi.fn(async () => {
      call += 1;
      // A failed open (attempt climbs to 1) …
      if (call === 1) return new Response(null, { status: 502 });
      // … then one that opens fine but drops with no end/gone: opening
      // resets the backoff, so THIS drop's retry is the base delay again,
      // not a continuation of the earlier count.
      if (call === 2) return sseResponse([frame("update", {})]);
      return sseResponse([frame("update", {}), frame("end", {})]);
    });
    const { scheduleRetry, delays } = immediateRetries();
    openRowStream("/x", () => {}, fetchImpl, scheduleRetry);
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(3));

    expect(delays).toEqual([RECONNECT_DELAYS_MS[0], RECONNECT_DELAYS_MS[0]]);
  });

  it("reconnects on a non-2xx answer too, not just a network failure", async () => {
    let call = 0;
    const fetchImpl = vi.fn(async () => {
      call += 1;
      if (call === 1) return new Response(null, { status: 502 });
      return sseResponse([frame("update", {}), frame("end", {})]);
    });
    const { scheduleRetry, delays } = immediateRetries();
    openRowStream("/x", () => {}, fetchImpl, scheduleRetry);
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
    expect(delays).toEqual([RECONNECT_DELAYS_MS[0]]);
  });

  it("stop() aborts the connection and cancels any pending reconnect", async () => {
    const fetchImpl = vi.fn(
      async () => sseResponse([frame("update", {})]), // always drops
    );
    const cancelled = vi.fn();
    const scheduleRetry = vi.fn(() => cancelled);
    const handle = openRowStream("/x", () => {}, fetchImpl, scheduleRetry);
    await vi.waitFor(() => expect(scheduleRetry).toHaveBeenCalledTimes(1));

    handle.stop();
    expect(cancelled).toHaveBeenCalledTimes(1);

    // A reconnect that was already scheduled (but not run, since this
    // fake never calls `run()`) must not fire after stop().
    const callsBefore = fetchImpl.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchImpl.mock.calls.length).toBe(callsBefore);
  });
});
