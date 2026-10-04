// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNow } from "@/lib/use-now";

describe("useNow", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("ticks every interval while active", () => {
    const start = Date.now();
    const { result } = renderHook(() => useNow(true, 1000));
    expect(result.current).toBe(start);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe(start + 1000);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current).toBe(start + 3000);
  });

  it("does not re-render while inactive", () => {
    let renders = 0;
    const start = Date.now();
    const { result } = renderHook(() => {
      renders += 1;
      return useNow(false);
    });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current).toBe(start);
    expect(renders).toBe(1);
  });

  it("refreshes immediately when it becomes active again", () => {
    const start = Date.now();
    const { result, rerender } = renderHook(({ on }) => useNow(on), {
      initialProps: { on: false },
    });
    vi.setSystemTime(start + 10_000);
    rerender({ on: true });
    act(() => {
      vi.advanceTimersByTime(0);
    });
    expect(result.current).toBe(start + 10_000);
  });

  it("stops ticking once inactive", () => {
    const { result, rerender } = renderHook(({ on }) => useNow(on), {
      initialProps: { on: true },
    });
    rerender({ on: false });
    const frozen = result.current;
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current).toBe(frozen);
  });
});
