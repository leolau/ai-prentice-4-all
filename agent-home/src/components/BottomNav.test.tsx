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
    const count = (html.match(/<li class="flex-1">/g) || []).length;
    expect(count).toBe(6);
  });

  // Badge tests (Part 5): zero open renders no badge; a count renders it.
  it("renders no badge when badgeCounts is empty (zero open)", () => {
    const html = renderToStaticMarkup(<BottomNav badgeCounts={{}} />);
    // The To-dos label is present, but no count bubble.
    expect(html).toContain("To-dos");
    // No rounded-full badge span with a number.
    expect(html).not.toMatch(/rounded-full[^>]*>\d+/);
  });

  it("renders the badge count when open to-dos exist", () => {
    const html = renderToStaticMarkup(
      <BottomNav badgeCounts={{ "todos-open": 3 }} />,
    );
    expect(html).toContain("To-dos");
    expect(html).toContain("3");
  });

  it("renders no badge when count is zero", () => {
    const html = renderToStaticMarkup(
      <BottomNav badgeCounts={{ "todos-open": 0 }} />,
    );
    expect(html).not.toMatch(/rounded-full[^>]*>0</);
  });
});
