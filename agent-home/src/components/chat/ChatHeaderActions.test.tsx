import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChatHeaderActions } from "@/components/chat/ChatHeaderActions";

describe("ChatHeaderActions", () => {
  it("renders the data-component root", () => {
    const html = renderToStaticMarkup(<ChatHeaderActions />);
    expect(html).toContain('data-component="ChatHeaderActions"');
  });

  it("renders the Archived button with correct aria-label", () => {
    const html = renderToStaticMarkup(<ChatHeaderActions />);
    expect(html).toContain("Archived");
    expect(html).toContain('aria-label="Show archived conversations"');
  });

  it("renders the + New button with correct aria-label", () => {
    const html = renderToStaticMarkup(<ChatHeaderActions />);
    expect(html).toContain("+ New");
    expect(html).toContain('aria-label="New conversation"');
  });
});
