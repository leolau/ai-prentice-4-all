// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UNAVAILABLE_RETRY_MS, useCardActivity } from "./useCardActivity";

function sse(frames: string): Response {
  return new Response(frames, { headers: { "content-type": "text/event-stream" } });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useCardActivity", () => {
  it("streams frames, then resumes from its cursor after the stream drops", async () => {
    const urls: string[] = [];
    const responses = [
      sse('event: reasoning\ndata: {"seq":4,"at":10,"text":"Reading"}\n\n'),
      sse('event: reasoning\ndata: {"seq":5,"at":11,"text":"Writing"}\n\nevent: end\ndata: {"cursor":5}\n\n'),
    ];
    const fetchImpl = vi.fn(async (url: string) => {
      urls.push(url);
      return responses.shift() ?? sse("");
    }) as unknown as typeof fetch;
    const { result } = renderHook(() => useCardActivity("mou", "t1", true, fetchImpl));
    await waitFor(() => expect(result.current.reasoning).toBe("Reading"));
    await waitFor(() => expect(result.current.ended).toBe(true), { timeout: 5_000 });
    expect(result.current.reasoning).toBe("Reading\nWriting");
    expect(urls).toEqual([
      "/api/projects/mou/cards/t1/activity?after=0",
      "/api/projects/mou/cards/t1/activity?after=4",
    ]);
  });

  it("asks again later when no worker is publishing yet", async () => {
    vi.useFakeTimers();
    const fetchImpl = vi.fn(async () =>
      sse('event: unavailable\ndata: {"reason":"x"}\n\n'),
    ) as unknown as typeof fetch;
    const { result } = renderHook(() => useCardActivity("mou", "t1", true, fetchImpl));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(result.current.unavailable).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(UNAVAILABLE_RETRY_MS + 10);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("does nothing for a card that is not live, and gives up on a 404", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 404 })) as unknown as typeof fetch;
    renderHook(() => useCardActivity("mou", "t1", false, fetchImpl));
    expect(fetchImpl).not.toHaveBeenCalled();
    vi.useFakeTimers();
    renderHook(() => useCardActivity("mou", "t2", true, fetchImpl));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
