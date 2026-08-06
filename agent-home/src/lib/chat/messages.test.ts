/**
 * Regression tests for the streaming-truncation bug: the chat reply used to
 * show only its first token ("It only shows the first couple of characters
 * then stopped") because the live-message update matched the placeholder by
 * object identity, which stops matching once the first delta replaces it.
 * `setLastAssistantContent` targets the trailing assistant turn by position,
 * so every delta and the final completed content land.
 */
import { describe, expect, it } from "vitest";

import { setLastAssistantContent, withLiveTurn } from "@/lib/chat/messages";
import type { ChatMessage } from "@/types";

describe("setLastAssistantContent", () => {
  it("applies every delta of a streamed turn (not just the first)", () => {
    let msgs: ChatMessage[] = [
      { role: "user", content: "What model are you?" },
      { role: "assistant", content: "" },
    ];
    let acc = "";
    for (const delta of ["I'm", " powered", " by", " GLM-5.2", "."]) {
      acc += delta;
      const before = msgs;
      msgs = setLastAssistantContent(msgs, acc);
      // Each update returns a NEW array (the placeholder is replaced), which is
      // exactly why identity-based matching failed after the first delta.
      expect(msgs).not.toBe(before);
    }

    expect(msgs[msgs.length - 1].content).toBe("I'm powered by GLM-5.2.");
    // The user turn is untouched.
    expect(msgs[0]).toEqual({ role: "user", content: "What model are you?" });
  });

  it("overwrites with the final completed content", () => {
    const msgs = setLastAssistantContent(
      [
        { role: "user", content: "hi" },
        { role: "assistant", content: "partial" },
      ],
      "the full final answer",
    );
    expect(msgs[1].content).toBe("the full final answer");
  });

  it("is a no-op when the last message is not an assistant turn", () => {
    const msgs: ChatMessage[] = [{ role: "user", content: "hi" }];
    expect(setLastAssistantContent(msgs, "x")).toBe(msgs);
  });

  it("is a no-op on an empty list", () => {
    const msgs: ChatMessage[] = [];
    expect(setLastAssistantContent(msgs, "x")).toBe(msgs);
  });

  it("returns the same reference when content is unchanged", () => {
    const msgs: ChatMessage[] = [
      { role: "assistant", content: "same" },
    ];
    expect(setLastAssistantContent(msgs, "same")).toBe(msgs);
  });
});

describe("withLiveTurn", () => {
  it("returns the base transcript unchanged when no turn is buffered", () => {
    const base: ChatMessage[] = [{ role: "user", content: "hi" }];
    expect(withLiveTurn(base, undefined)).toBe(base);
  });

  it("overlays a buffered turn (user + partial assistant) onto the transcript", () => {
    const base: ChatMessage[] = [
      { role: "user", content: "earlier" },
      { role: "assistant", content: "earlier reply" },
    ];
    const out = withLiveTurn(base, { user: "new question", assistant: "partial" });
    // Base is preserved and the live turn is appended in order, with the
    // assistant bubble last so streaming deltas keep landing on it.
    expect(out).toEqual([
      ...base,
      { role: "user", content: "new question" },
      { role: "assistant", content: "partial" },
    ]);
    expect(out[out.length - 1].role).toBe("assistant");
  });
});
