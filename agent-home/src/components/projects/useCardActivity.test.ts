/**
 * Folding a card's live activity frames, and the hook's resume/retry
 * contract: reconnects resume from the cursor, `unavailable` is asked
 * again later, `end` stops for good.
 */
import { describe, expect, it } from "vitest";

import {
  EMPTY_CARD_ACTIVITY,
  MAX_REASONING_CHARS,
  MAX_TOOLS,
  applyCardActivityFrame,
} from "./useCardActivity";

const fold = (frames: { event: string; data: Record<string, unknown> }[]) =>
  frames.reduce(applyCardActivityFrame, EMPTY_CARD_ACTIVITY);

describe("applyCardActivityFrame", () => {
  it("accumulates reasoning and status lines, tracking cursor and time", () => {
    const s = fold([
      { event: "status", data: { seq: 1, at: 100, text: "Worker started" } },
      { event: "reasoning", data: { seq: 2, at: 105, text: "Reading the template" } },
    ]);
    expect(s.reasoning).toBe("Worker started\nReading the template");
    expect(s.cursor).toBe(2);
    expect(s.lastAt).toBe(105);
  });

  it("marks the chip a completion belongs to and keeps unmatched ones", () => {
    const s = fold([
      { event: "tool.start", data: { seq: 1, tool_id: "tc-1", name: "read_file" } },
      { event: "tool.start", data: { seq: 2, tool_id: "tc-2", name: "terminal" } },
      { event: "tool.complete", data: { seq: 3, tool_id: "tc-1", name: "read_file" } },
      { event: "tool.end", data: { seq: 4, tool_id: "tc-9", name: "web_search" } },
    ]);
    expect(s.tools).toEqual([
      { id: "tc-1", name: "read_file", done: true },
      { id: "tc-2", name: "terminal", done: false },
      { id: "tc-9", name: "web_search", done: true },
    ]);
  });

  it("never renders anything but a tool's name", () => {
    const s = fold([
      { event: "tool.start", data: { tool_id: "tc-1", name: "terminal", args: { cmd: "cat .env" }, result: "SECRET" } },
    ]);
    expect(JSON.stringify(s)).not.toContain("SECRET");
    expect(JSON.stringify(s)).not.toContain(".env");
  });

  it("records unavailable and end as states; a later event clears unavailable", () => {
    let s = fold([{ event: "unavailable", data: { reason: "x" } }]);
    expect(s.unavailable).toBe(true);
    s = applyCardActivityFrame(s, { event: "reasoning", data: { seq: 1, text: "now publishing" } });
    expect(s.unavailable).toBe(false);
    s = applyCardActivityFrame(s, { event: "end", data: { cursor: 1 } });
    expect(s.ended).toBe(true);
  });

  it("never moves the cursor or the clock backwards", () => {
    const s = fold([
      { event: "reasoning", data: { seq: 5, at: 200, text: "b" } },
      { event: "reasoning", data: { seq: 3, at: 150, text: "a" } },
    ]);
    expect(s.cursor).toBe(5);
    expect(s.lastAt).toBe(200);
  });

  it("stays bounded on a long run", () => {
    const frames = Array.from({ length: 200 }, (_, i) => ({
      event: i % 2 ? "reasoning" : "tool.start",
      data: { seq: i + 1, tool_id: `t${i}`, name: "x", text: "y".repeat(100) },
    }));
    const s = fold(frames);
    expect(s.reasoning.length).toBeLessThanOrEqual(MAX_REASONING_CHARS);
    expect(s.tools.length).toBe(MAX_TOOLS);
  });

  it("ignores frames it does not know and empty text", () => {
    const s = fold([
      { event: "mystery", data: { seq: 9 } },
      { event: "reasoning", data: { seq: 1, text: "" } },
    ]);
    expect(s.reasoning).toBe("");
    expect(s.cursor).toBe(1);
  });
});
