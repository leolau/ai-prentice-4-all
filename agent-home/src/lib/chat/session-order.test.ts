/**
 * Archiving the open conversation moves to a neighbour in display order,
 * never into a blank "New conversation" while others remain.
 */
import { describe, expect, it } from "vitest";

import { nextActiveAfterArchive } from "@/lib/chat/session-order";

describe("nextActiveAfterArchive", () => {
  it("picks the conversation after the archived tab", () => {
    expect(nextActiveAfterArchive(["a", "b", "c"], "b")).toBe("c");
  });

  it("falls back to the previous tab when archiving the last one", () => {
    expect(nextActiveAfterArchive(["a", "b", "c"], "c")).toBe("b");
  });

  it("picks the new first tab when archiving the first one", () => {
    expect(nextActiveAfterArchive(["a", "b", "c"], "a")).toBe("b");
  });

  it("returns null (empty state) when nothing remains", () => {
    expect(nextActiveAfterArchive(["a"], "a")).toBeNull();
  });

  it("returns the first remaining tab when the archived id is unknown", () => {
    expect(nextActiveAfterArchive(["a", "b"], "z")).toBe("a");
  });
});
