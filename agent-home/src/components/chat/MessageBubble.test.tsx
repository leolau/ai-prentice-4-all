import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";

describe("MessageBubble", () => {
  it("renders a user turn aligned right with its text", () => {
    const html = renderToStaticMarkup(
      <MessageBubble message={{ role: "user", content: "hello agent" }} />,
    );
    expect(html).toContain('data-component="MessageBubble"');
    expect(html).toContain("justify-end");
    expect(html).toContain("hello agent");
  });

  it("renders an assistant turn aligned left", () => {
    const html = renderToStaticMarkup(
      <MessageBubble message={{ role: "assistant", content: "how can I help?" }} />,
    );
    expect(html).toContain("justify-start");
    expect(html).toContain("how can I help?");
  });

  it("renders assistant Markdown (headings, tables, emphasis) as HTML", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        message={{
          role: "assistant",
          content:
            "## Tenders\n\n**Bold** and a table:\n\n| Name | Due |\n| --- | --- |\n| CUHK | Today |",
        }}
      />,
    );
    expect(html).toContain("<h2");
    expect(html).toContain("<strong>Bold</strong>");
    expect(html).toContain("<table");
    expect(html).toContain("<th");
    expect(html).toContain("CUHK");
  });

  it("sanitizes dangerous HTML in an assistant reply (fail-closed)", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        message={{
          role: "assistant",
          content:
            'Safe <b>bold</b> but <script>alert(1)</script> and <a href="javascript:alert(2)">x</a> and <img src="x" onerror="alert(3)">',
        }}
      />,
    );
    // Allowed inline HTML survives; scripts, javascript: URLs and inline event
    // handlers are stripped by rehype-sanitize.
    expect(html).toContain("<b>bold</b>");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("onerror");
  });

  it("routes an assistant private-bucket media ref through the signing route", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        message={{
          role: "assistant",
          content: "here ![shot](/api/chat/media?path=mia_member%2Fhome_2%2Fu1-a.png)",
        }}
      />,
    );
    expect(html).toContain('data-component="ChatMedia"');
    expect(html).not.toContain("<img");
  });

  it("routes a private-bucket media ref through the signing route", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        message={{
          role: "user",
          content:
            "look ![shot](/api/chat/media?path=mia_member%2Fhome_2%2Fu1-a.png)",
        }}
      />,
    );
    expect(html).toContain('data-component="ChatMedia"');
    // The private object path is never rendered as an image src — the signed
    // URL is fetched from the BFF after the server-side ownership check.
    expect(html).not.toContain("<img");
    expect(html).toContain("Loading shot");
  });

  it("renders inline image attachments as <img> media", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        message={{
          role: "user",
          content: "look at this ![shot](https://cdn.test/a.png)",
        }}
      />,
    );
    expect(html).toContain("look at this");
    expect(html).toContain('src="https://cdn.test/a.png"');
    expect(html).toContain('alt="shot"');
  });
});
