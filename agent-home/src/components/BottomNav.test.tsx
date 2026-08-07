import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// BottomNav uses usePathname — mock to a neutral path so no tab is active.
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
}));

import { BottomNav } from "@/components/BottomNav";
import { PRIMARY_NAV } from "@/components/nav-items";

describe("BottomNav", () => {
  it("renders the data-component root", () => {
    const html = renderToStaticMarkup(<BottomNav />);
    expect(html).toContain('data-component="BottomNav"');
  });

  it("renders all primary nav labels", () => {
    const html = renderToStaticMarkup(<BottomNav />);
    for (const tab of PRIMARY_NAV) {
      expect(html).toContain(tab.label);
    }
  });

  it("renders the 6th More overflow tab with the ⋯ glyph", () => {
    const html = renderToStaticMarkup(<BottomNav />);
    expect(html).toContain("More");
    expect(html).toContain("⋯");
  });

  it("has 6 flex-1 list items (5 primary + 1 More)", () => {
    const html = renderToStaticMarkup(<BottomNav />);
    // Count occurrences of "flex-1" in list items
    const count = (html.match(/<li class="flex-1">/g) || []).length;
    expect(count).toBe(6);
  });
});
