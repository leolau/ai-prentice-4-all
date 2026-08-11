import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BusyRegion } from "@/components/ui/BusyRegion";

/**
 * The contract callers depend on: while a region waits on the backend it must
 * (a) say so, and (b) sit an overlay over its own controls so a second tap
 * can't fire a duplicate request. When idle it must add neither.
 */
describe("BusyRegion", () => {
  it("keeps the region untouched when it is not busy", () => {
    const html = renderToStaticMarkup(
      <BusyRegion busy={false} label="Saving…">
        <button type="button">Undo</button>
      </BusyRegion>,
    );
    expect(html).toContain("Undo");
    expect(html).toContain('aria-busy="false"');
    // No overlay element and no status text competing with the content.
    expect(html).not.toContain("Saving…");
    expect(html).not.toContain('role="status"');
  });

  it("announces the wait and covers the region while busy", () => {
    const html = renderToStaticMarkup(
      <BusyRegion busy label="Applying your change…">
        <button type="button">Undo</button>
      </BusyRegion>,
    );
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("Applying your change…");
    expect(html).toContain('role="status"');
    // The blocking layer covers the region it wraps, not the whole viewport,
    // so the nav stays reachable during a slow request.
    expect(html).toContain("absolute inset-0");
    expect(html).not.toContain("fixed inset-0");
    // The content is still rendered underneath — this is an overlay, not a
    // replacement, so the user keeps their place on the page.
    expect(html).toContain("Undo");
  });

  it("starts transparent so a fast round-trip never flashes a spinner", () => {
    // Server render == first client frame: the layer is mounted (blocking
    // immediately) but invisible until the wait outlasts the delay.
    const html = renderToStaticMarkup(
      <BusyRegion busy label="Loading…">
        <span>rows</span>
      </BusyRegion>,
    );
    expect(html).toContain("opacity-0");
  });
});
