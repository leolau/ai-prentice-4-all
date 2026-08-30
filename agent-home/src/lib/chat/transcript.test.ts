import { describe, expect, it } from "vitest";

import {
  COMPACTION_END_MARKER,
  MERGED_PRIOR_CONTEXT_HEADER,
  MERGED_SUMMARY_DELIMITER,
  splitCompactionContent,
  stripUiContextLine,
  visibleTurns,
} from "@/lib/chat/transcript";
import type { ChatMessage } from "@/types";

const SUMMARY_BODY =
  "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted.\n" +
  "## Active Task\nUser asked about the project run.";

describe("splitCompactionContent", () => {
  it("hides a standalone summary entirely", () => {
    const split = splitCompactionContent(
      `${SUMMARY_BODY}\n\n${COMPACTION_END_MARKER}`,
    );
    expect(split.hadCompaction).toBe(true);
    expect(split.display).toBe("");
  });

  it("keeps the original reply after a legacy mid-marker merge", () => {
    const split = splitCompactionContent(
      `${SUMMARY_BODY}\n\n${COMPACTION_END_MARKER}\n\nThe run is healthy.`,
    );
    expect(split.hadCompaction).toBe(true);
    expect(split.display).toBe("The run is healthy.");
  });

  it("keeps only the prior content from a current header+delimiter merge", () => {
    const split = splitCompactionContent(
      `${MERGED_PRIOR_CONTEXT_HEADER}\nThe run is healthy.\n\n` +
        `${MERGED_SUMMARY_DELIMITER}\n\n${SUMMARY_BODY}\n\n${COMPACTION_END_MARKER}`,
    );
    expect(split.hadCompaction).toBe(true);
    expect(split.display).toBe("The run is healthy.");
  });

  it("treats a header without delimiter as fully internal", () => {
    const split = splitCompactionContent(
      `${MERGED_PRIOR_CONTEXT_HEADER}\nold content only`,
    );
    expect(split.hadCompaction).toBe(true);
    expect(split.display).toBe("");
  });

  it("recognizes the legacy prefix", () => {
    const split = splitCompactionContent("[CONTEXT SUMMARY]: old turns");
    expect(split.hadCompaction).toBe(true);
    expect(split.display).toBe("");
  });

  it("passes normal content through untouched", () => {
    const split = splitCompactionContent("Can you check the project?");
    expect(split.hadCompaction).toBe(false);
    expect(split.display).toBe("Can you check the project?");
  });
});

describe("stripUiContextLine", () => {
  it("removes only the leading app-context line", () => {
    const stripped = stripUiContextLine(
      "[app context: page /projects/x · last active: none]\nCan you check?",
    );
    expect(stripped).toBe("Can you check?");
  });

  it("leaves mid-text mentions untouched", () => {
    const text = "hello\n[app context: page /x · last active: none]\nbye";
    expect(stripUiContextLine(text)).toBe(text);
  });

  it("returns empty string when the message was only the context line", () => {
    expect(
      stripUiContextLine("[app context: page /x · last active: none]"),
    ).toBe("");
  });
});

describe("visibleTurns", () => {
  it("drops tool and system rows", () => {
    const msgs: ChatMessage[] = [
      { role: "user", content: "hi" },
      { role: "tool", content: "{}" },
      { role: "system", content: "prompt" },
      { role: "assistant", content: "hello" },
    ];
    expect(visibleTurns(msgs).map((m) => m.role)).toEqual(["user", "assistant"]);
  });
});
