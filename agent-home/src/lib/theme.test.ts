import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyTheme,
  DEFAULT_THEME,
  isThemeId,
  readStoredTheme,
  THEME_STORAGE_KEY,
} from "@/lib/theme";

// The test runner uses the `node` environment (no DOM), so stub the minimal
// `window.localStorage` and `document.documentElement` the theme helpers touch.
function fakeStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => (map.has(k) ? (map.get(k) as string) : null),
    setItem: (k: string, v: string) => void map.set(k, v),
    clear: () => map.clear(),
  };
}

beforeEach(() => {
  vi.stubGlobal("window", { localStorage: fakeStorage() });
  vi.stubGlobal("document", { documentElement: { dataset: {} } });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("theme", () => {
  it("recognises only the three known theme ids", () => {
    expect(isThemeId("dark")).toBe(true);
    expect(isThemeId("light")).toBe(true);
    expect(isThemeId("colourful")).toBe(true);
    expect(isThemeId("neon")).toBe(false);
    expect(isThemeId(null)).toBe(false);
  });

  it("falls back to the default when nothing (or junk) is stored", () => {
    expect(readStoredTheme()).toBe(DEFAULT_THEME);
    window.localStorage.setItem(THEME_STORAGE_KEY, "bogus");
    expect(readStoredTheme()).toBe(DEFAULT_THEME);
  });

  it("applyTheme sets the document attribute and persists the choice", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(readStoredTheme()).toBe("light");
  });
});
