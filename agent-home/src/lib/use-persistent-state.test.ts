// @vitest-environment jsdom
/**
 * Regression tests for usePersistentState. The snapshot cache matters:
 * `useSyncExternalStore` treats every NEW reference from getSnapshot as a
 * store change, so a parse that builds an object must still yield the same
 * reference while the stored string is unchanged — otherwise components with
 * object-valued persistent state loop forever ("Maximum update depth").
 */
import { cleanup, renderHook, act } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { usePersistentState } from "@/lib/use-persistent-state";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

interface Rect {
  x: number;
  y: number;
}

describe("usePersistentState", () => {
  it("returns the fallback on the server-shaped first read and persists writes", () => {
    const { result } = renderHook(() =>
      usePersistentState<Rect | null>(
        "test:rect",
        null,
        (raw) => JSON.parse(raw) as Rect | null,
        (v) => JSON.stringify(v),
      ),
    );
    expect(result.current[0]).toBeNull();
    act(() => result.current[1]({ x: 1, y: 2 }));
    expect(result.current[0]).toEqual({ x: 1, y: 2 });
    expect(JSON.parse(localStorage.getItem("test:rect") ?? "null")).toEqual({
      x: 1,
      y: 2,
    });
  });

  it("keeps a stable reference for object values across re-renders", () => {
    localStorage.setItem("test:rect", JSON.stringify({ x: 3, y: 4 }));
    const { result, rerender } = renderHook(() =>
      usePersistentState<Rect | null>(
        "test:rect",
        null,
        (raw) => JSON.parse(raw) as Rect | null,
        (v) => JSON.stringify(v),
      ),
    );
    const first = result.current[0];
    rerender();
    rerender();
    // Same reference — a fresh parse per call would loop useSyncExternalStore.
    expect(result.current[0]).toBe(first);
    expect(first).toEqual({ x: 3, y: 4 });
  });
});
