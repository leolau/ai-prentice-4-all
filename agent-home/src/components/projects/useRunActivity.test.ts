/**
 * Folding a run's activity frames into what the page shows.
 *
 * The contract, not the markup: reasoning accumulates in arrival order, a
 * tool's completion marks the chip it opened instead of adding a second one,
 * and "the box has no live view of this run" is a state the page can say out
 * loud rather than an empty panel that reads as an idle run.
 */
import { describe, expect, it } from "vitest";

import { applyActivityFrame } from "./useRunActivity";

const EMPTY = { reasoning: "", tools: [], unavailable: false };

describe("applyActivityFrame", () => {
  it("accumulates reasoning and status lines in arrival order", () => {
    let s = applyActivityFrame(EMPTY, {
      event: "status",
      data: { text: "Starting 2 inline step(s): read, write" },
    });
    s = applyActivityFrame(s, {
      event: "reasoning",
      data: { text: "Reading last week's digest" },
    });
    expect(s.reasoning).toBe(
      "Starting 2 inline step(s): read, write\nReading last week's digest",
    );
  });

  it("marks the chip a completion belongs to rather than appending one", () => {
    let s = applyActivityFrame(EMPTY, {
      event: "tool.start",
      data: { tool_id: "tc-1", name: "read_file" },
    });
    s = applyActivityFrame(s, {
      event: "tool.start",
      data: { tool_id: "tc-2", name: "terminal" },
    });
    s = applyActivityFrame(s, {
      event: "tool.complete",
      data: { tool_id: "tc-1", name: "read_file" },
    });
    expect(s.tools).toEqual([
      { id: "tc-1", name: "read_file", done: true },
      { id: "tc-2", name: "terminal", done: false },
    ]);
  });

  it("still shows a completion whose start it never saw", () => {
    // A reader that joined mid-run after the buffer dropped the start.
    const s = applyActivityFrame(EMPTY, {
      event: "tool.complete",
      data: { tool_id: "tc-9", name: "web_search" },
    });
    expect(s.tools).toEqual([{ id: "tc-9", name: "web_search", done: true }]);
  });

  it("records unavailability as a state, not as nothing happening", () => {
    const s = applyActivityFrame(EMPTY, {
      event: "unavailable",
      data: { reason: "not running in this process" },
    });
    expect(s.unavailable).toBe(true);
  });

  it("ignores a frame it does not understand", () => {
    const s = applyActivityFrame(EMPTY, { event: "end", data: { cursor: 4 } });
    expect(s).toEqual(EMPTY);
  });
});
