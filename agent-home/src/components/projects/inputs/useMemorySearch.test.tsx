// @vitest-environment jsdom
/**
 * Memory search: debounced (one request for a burst of typing), blank shows
 * nothing, stale answers never overwrite newer ones; and the picker seeds
 * its suggestions from the goal and keeps ticked rows listed.
 */
import { act, cleanup, fireEvent, render, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MemoryPicker } from "@/components/projects/inputs/MemoryPicker";
import {
  MEMORY_SEARCH_DEBOUNCE_MS,
  useMemorySearch,
} from "@/components/projects/inputs/useMemorySearch";
import type { MemoryRow } from "@/types";

function row(id: string, text: string): MemoryRow {
  return {
    id,
    owner_user_id: "leo",
    visibility: "private",
    kind: "fact",
    topic: null,
    text,
    truncated: false,
    created_at: null,
    uses: 0,
    last_used: null,
    elevated: false,
    provenance: "user",
    score: 0.8,
  };
}

function answering(map: Record<string, MemoryRow[]>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const q = new URL(String(input), "http://x").searchParams.get("q") ?? "";
    return new Response(JSON.stringify({ rows: map[q] ?? [], total: 0, limit: 6, offset: 0 }), {
      status: 200,
    });
  }) as unknown as ReturnType<typeof vi.fn> & typeof fetch;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useMemorySearch", () => {
  it("debounces a burst of typing into one request", async () => {
    vi.useFakeTimers();
    const fetchImpl = answering({ connectar: [row("m1", "ConnectAR Ltd")] });
    const { result, rerender } = renderHook(({ q }) => useMemorySearch(q, { fetchImpl }), {
      initialProps: { q: "c" },
    });
    rerender({ q: "conn" });
    rerender({ q: "connectar" });
    expect(result.current.loading).toBe(true);
    await act(async () => {
      vi.advanceTimersByTime(MEMORY_SEARCH_DEBOUNCE_MS - 1);
    });
    expect(fetchImpl).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    vi.useRealTimers();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(String(fetchImpl.mock.calls[0][0])).toBe("/api/memory/rows?q=connectar&limit=6");
    expect(result.current.results.map((r) => r.id)).toEqual(["m1"]);
    expect(result.current.answered).toBe("connectar");
  });

  it("a blank query shows nothing and asks nothing", async () => {
    const fetchImpl = answering({});
    const { result } = renderHook(() => useMemorySearch("   ", { fetchImpl, debounceMs: 0 }));
    await new Promise((r) => setTimeout(r, 5));
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(result.current).toEqual({ results: [], loading: false, error: null, answered: "" });
  });

  it("a failed search says so", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 500 })) as unknown as typeof fetch;
    const { result } = renderHook(() => useMemorySearch("x", { fetchImpl, debounceMs: 0 }));
    await waitFor(() => expect(result.current.error).toBe("Could not search your memory just now."));
  });

  it("a stale answer never overwrites a newer one", async () => {
    let releaseOld!: () => void;
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const q = new URL(String(input), "http://x").searchParams.get("q");
      if (q === "old") await new Promise<void>((r) => (releaseOld = r));
      return new Response(JSON.stringify({ rows: [row(q ?? "", q ?? "")] }), { status: 200 });
    }) as unknown as typeof fetch;
    const { result, rerender } = renderHook(({ q }) => useMemorySearch(q, { fetchImpl, debounceMs: 0 }), {
      initialProps: { q: "old" },
    });
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    rerender({ q: "new" });
    await waitFor(() => expect(result.current.answered).toBe("new"));
    await act(async () => releaseOld());
    expect(result.current.results.map((r) => r.id)).toEqual(["new"]);
  });
});

describe("MemoryPicker", () => {
  it("suggests from the goal before anything is typed, and ticking selects", async () => {
    const fetchImpl = answering({ "Draft the ConnectAR MOUs": [row("m1", "ConnectAR Ltd company details")] });
    const onToggle = vi.fn();
    const utils = render(
      <MemoryPicker
        suggestFrom="Draft the ConnectAR MOUs"
        selected={new Map()}
        onToggle={onToggle}
        fetchImpl={fetchImpl}
        debounceMs={0}
      />,
    );
    const box = await utils.findByLabelText("ConnectAR Ltd company details");
    expect(utils.getByText(/Suggested from your goal/)).toBeTruthy();
    fireEvent.click(box);
    expect(onToggle).toHaveBeenCalledWith(expect.objectContaining({ id: "m1" }), true);
  });

  it("typing replaces the suggestion search, and ticked rows stay listed", async () => {
    const fetchImpl = answering({ pricing: [row("m2", "2025 pricing sheet")] });
    const selected = new Map([["m1", row("m1", "ConnectAR Ltd company details")]]);
    const utils = render(
      <MemoryPicker
        suggestFrom="goal text"
        selected={selected}
        onToggle={() => {}}
        fetchImpl={fetchImpl}
        debounceMs={0}
      />,
    );
    fireEvent.change(utils.getByLabelText("Search memory"), { target: { value: "pricing" } });
    await utils.findByLabelText("2025 pricing sheet");
    expect((utils.getByLabelText("ConnectAR Ltd company details") as HTMLInputElement).checked).toBe(true);
    expect(utils.queryByText(/Suggested from your goal/)).toBeNull();
    const queries = fetchImpl.mock.calls.map(([u]) => new URL(String(u), "http://x").searchParams.get("q"));
    expect(queries[queries.length - 1]).toBe("pricing");
  });

  it("says when nothing matches", async () => {
    const fetchImpl = answering({});
    const utils = render(<MemoryPicker fetchImpl={fetchImpl} debounceMs={0} renderAction={() => null} />);
    fireEvent.change(utils.getByLabelText("Search memory"), { target: { value: "zzz" } });
    expect(await utils.findByText("No memories match.")).toBeTruthy();
  });
});
