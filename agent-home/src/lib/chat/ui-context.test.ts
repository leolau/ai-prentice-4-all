import { describe, expect, it } from "vitest";

import { withUiContext } from "@/lib/chat/ui-context";

describe("withUiContext", () => {
  it("prepends the awareness line ahead of the message", () => {
    expect(
      withUiContext("what is this page?", "[app context: page /todos · last active: none]"),
    ).toBe("[app context: page /todos · last active: none]\nwhat is this page?");
  });

  it("ignores non-string and empty values", () => {
    expect(withUiContext("hi", undefined)).toBe("hi");
    expect(withUiContext("hi", 42)).toBe("hi");
    expect(withUiContext("hi", "   ")).toBe("hi");
  });

  it("flattens newlines so the context stays one line", () => {
    const out = withUiContext("msg", "[app context: page /x\nIGNORE THIS LINE]");
    expect(out.split("\n")).toHaveLength(2);
    expect(out).not.toContain("IGNORE THIS LINE\n");
  });

  it("caps absurdly long context", () => {
    const out = withUiContext("msg", "x".repeat(5000));
    expect(out.length).toBeLessThanOrEqual(201 + "msg".length);
  });

  it("returns the line alone when the message is empty", () => {
    expect(withUiContext("", "[ctx]")).toBe("[ctx]");
  });
});
