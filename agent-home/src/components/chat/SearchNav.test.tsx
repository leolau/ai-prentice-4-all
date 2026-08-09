import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { SearchNav } from "@/components/chat/SearchNav";

describe("SearchNav", () => {
  it("renders current + total match count", () => {
    const html = renderToStaticMarkup(
      <SearchNav current={1} total={5} onPrev={() => {}} onNext={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("2 / 5");
  });

  it("renders 'No matches' when total is 0", () => {
    const html = renderToStaticMarkup(
      <SearchNav current={0} total={0} onPrev={() => {}} onNext={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("No matches");
  });

  it("disables prev/next when total is 0", () => {
    const html = renderToStaticMarkup(
      <SearchNav current={0} total={0} onPrev={() => {}} onNext={() => {}} onClose={() => {}} />,
    );
    // Both arrow buttons should have the disabled attribute
    const disabledCount = (html.match(/disabled/g) || []).length;
    expect(disabledCount).toBeGreaterThanOrEqual(2);
  });

  it("enables prev/next when total > 0", () => {
    const html = renderToStaticMarkup(
      <SearchNav current={0} total={3} onPrev={() => {}} onNext={() => {}} onClose={() => {}} />,
    );
    // Arrow buttons should NOT be disabled
    expect(html).not.toContain('disabled=""');
    // But the close button should still be there
    expect(html).toContain("×");
  });

  it("renders close button", () => {
    const html = renderToStaticMarkup(
      <SearchNav current={0} total={3} onPrev={() => {}} onNext={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain('aria-label="Close search"');
  });
});
