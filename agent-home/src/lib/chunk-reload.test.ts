/**
 * Stale-bundle chunk-load recovery — the production incident this fixes:
 * an installed iOS PWA left open across a deploy threw an unrecoverable
 * "Application error" instead of just fetching the current bundle.
 */
import { describe, expect, it, vi } from "vitest";

import {
  isChunkLoadError,
  markAppHealthy,
  reloadOnceForChunkError,
  type ReloadWindow,
} from "@/lib/chunk-reload";

describe("isChunkLoadError", () => {
  it("recognises webpack's own ChunkLoadError", () => {
    const err = new Error("Loading chunk 42 failed.");
    err.name = "ChunkLoadError";
    expect(isChunkLoadError(err)).toBe(true);
  });

  it("recognises the message pattern even with a generic error name", () => {
    expect(isChunkLoadError(new Error("Loading chunk 7 failed."))).toBe(true);
    expect(isChunkLoadError(new Error("Loading CSS chunk 3 failed."))).toBe(true);
  });

  it("recognises the browser-specific dynamic-import phrasings", () => {
    expect(
      isChunkLoadError(new Error("Failed to fetch dynamically imported module: /x.js")),
    ).toBe(true);
    expect(
      isChunkLoadError(new TypeError("error loading dynamically imported module")),
    ).toBe(true);
    // Safari/WebKit's exact wording — the one the real incident hit.
    expect(
      isChunkLoadError(new Error("Importing a module script failed")),
    ).toBe(true);
  });

  it("does not flag an ordinary application error", () => {
    expect(isChunkLoadError(new TypeError("Cannot read properties of null"))).toBe(
      false,
    );
    expect(isChunkLoadError(new Error("network request failed"))).toBe(false);
  });

  it("handles non-Error values and nullish input without throwing", () => {
    expect(isChunkLoadError(null)).toBe(false);
    expect(isChunkLoadError(undefined)).toBe(false);
    expect(isChunkLoadError("Loading chunk 1 failed")).toBe(true);
    expect(isChunkLoadError("just a string")).toBe(false);
  });
});

function fakeWindow(initial: Record<string, string> = {}): {
  win: ReloadWindow;
  store: Record<string, string>;
  reload: ReturnType<typeof vi.fn>;
} {
  const store = { ...initial };
  const reload = vi.fn();
  const win: ReloadWindow = {
    sessionStorage: {
      getItem: (k) => store[k] ?? null,
      setItem: (k, v) => {
        store[k] = v;
      },
      removeItem: (k) => {
        delete store[k];
      },
    },
    location: { reload },
  };
  return { win, store, reload };
}

describe("reloadOnceForChunkError", () => {
  it("reloads exactly once for a chunk error, then refuses a second time", () => {
    const { win, reload } = fakeWindow();
    const err = new Error("Loading chunk 1 failed.");

    expect(reloadOnceForChunkError(err, win)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);

    // Same tab, same session — a second chunk error must not loop.
    expect(reloadOnceForChunkError(err, win)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("never reloads for a non-chunk error", () => {
    const { win, reload } = fakeWindow();
    expect(reloadOnceForChunkError(new Error("boom"), win)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it("is a no-op with no window (SSR)", () => {
    expect(reloadOnceForChunkError(new Error("Loading chunk 1 failed."), null)).toBe(
      false,
    );
  });

  it("markAppHealthy clears the flag so a later deploy gets its own retry", () => {
    const { win, reload } = fakeWindow({ "agent-home:chunk-reload-attempted": "1" });
    const err = new Error("Loading chunk 1 failed.");

    // Blocked — today's flag is already set.
    expect(reloadOnceForChunkError(err, win)).toBe(false);

    markAppHealthy(win);
    expect(reloadOnceForChunkError(err, win)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("still reloads even if sessionStorage throws (private mode, quota)", () => {
    const reload = vi.fn();
    const win: ReloadWindow = {
      sessionStorage: {
        getItem: () => {
          throw new Error("storage disabled");
        },
        setItem: () => {
          throw new Error("storage disabled");
        },
        removeItem: () => {
          throw new Error("storage disabled");
        },
      },
      location: { reload },
    };
    expect(reloadOnceForChunkError(new Error("Loading chunk 1 failed."), win)).toBe(
      true,
    );
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
