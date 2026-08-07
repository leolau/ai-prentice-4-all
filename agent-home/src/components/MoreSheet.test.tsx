import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// MoreSheet uses usePathname — mock it to a fixed path so SSR works.
vi.mock("next/navigation", () => ({
  usePathname: () => "/files",
}));

import { MoreSheet } from "@/components/MoreSheet";
import { SECONDARY_NAV } from "@/components/nav-items";

describe("MoreSheet", () => {
  it("renders the data-component root and a More heading", () => {
    const html = renderToStaticMarkup(<MoreSheet onClose={() => {}} />);
    expect(html).toContain('data-component="MoreSheet"');
    expect(html).toContain("More");
  });

  it("renders every SECONDARY_NAV label", () => {
    const html = renderToStaticMarkup(<MoreSheet onClose={() => {}} />);
    for (const item of SECONDARY_NAV) {
      expect(html).toContain(item.label);
    }
  });

  it("marks the active route (matching usePathname)", () => {
    const html = renderToStaticMarkup(<MoreSheet onClose={() => {}} />);
    // /files is the mocked pathname; it should be aria-current="page"
    expect(html).toContain('aria-current="page"');
  });

  it("renders a Close button", () => {
    const html = renderToStaticMarkup(<MoreSheet onClose={() => {}} />);
    expect(html).toContain("Close");
  });
});
