import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PageSkeleton } from "@/components/ui/PageSkeleton";
import { Spinner } from "@/components/ui/Spinner";

describe("Spinner", () => {
  it("is decorative, leaving the label to whatever renders it", () => {
    const html = renderToStaticMarkup(<Spinner />);

    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain("animate-spin");
    // Inherits the caller's tone instead of hardcoding one, so it reads in
    // every theme.
    expect(html).toContain("border-current");
    expect(html).not.toContain('role="status"');
  });
});

describe("PageSkeleton", () => {
  it("announces the wait and shows placeholder rows for the page shape", () => {
    const html = renderToStaticMarkup(<PageSkeleton rows={3} label="Loading your inbox…" />);

    expect(html).toContain('role="status"');
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain("Loading your inbox…");
    // The rows are scenery for the announcement, so they stay out of the
    // accessibility tree.
    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain("animate-pulse");
    expect(html.match(/bg-\[var\(--color-surface\)\] p-4/g)?.length).toBe(3);
  });
});
