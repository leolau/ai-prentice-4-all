import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MemoryHeaderActions } from "@/components/memory/MemoryHeaderActions";

describe("MemoryHeaderActions", () => {
  it("renders the Legend button", () => {
    const html = renderToStaticMarkup(<MemoryHeaderActions />);
    expect(html).toContain(">Legend</button>");
  });

  it("renders the data-component root with an aria-label", () => {
    const html = renderToStaticMarkup(<MemoryHeaderActions />);
    expect(html).toContain('data-component="MemoryHeaderActions"');
    expect(html).toContain('aria-label="Map legend"');
  });
});
