import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TagFilterBar } from "@/components/chat/TagFilterBar";
import type { SessionTag } from "@/types";

const TAGS: SessionTag[] = [
  { id: "t1", name: "bug", color: "red" },
  { id: "t2", name: "feature", color: "green" },
  { id: "t3", name: "urgent", color: "amber" },
];

describe("TagFilterBar", () => {
  it("renders nothing when no tags", () => {
    const html = renderToStaticMarkup(
      <TagFilterBar
        tags={[]}
        includeTags={[]}
        excludeTags={[]}
        matchMode="any"
        onToggle={() => {}}
        onMatchModeChange={() => {}}
      />,
    );
    expect(html).toBe("");
  });

  it("renders tag chips for each tag", () => {
    const html = renderToStaticMarkup(
      <TagFilterBar
        tags={TAGS}
        includeTags={[]}
        excludeTags={[]}
        matchMode="any"
        onToggle={() => {}}
        onMatchModeChange={() => {}}
      />,
    );
    expect(html).toContain("bug");
    expect(html).toContain("feature");
    expect(html).toContain("urgent");
  });

  it("does not render OR/AND toggle when no tags are active", () => {
    const html = renderToStaticMarkup(
      <TagFilterBar
        tags={TAGS}
        includeTags={[]}
        excludeTags={[]}
        matchMode="any"
        onToggle={() => {}}
        onMatchModeChange={() => {}}
      />,
    );
    expect(html).not.toContain(">OR<");
    expect(html).not.toContain(">AND<");
  });

  it("renders OR toggle when a tag is included", () => {
    const html = renderToStaticMarkup(
      <TagFilterBar
        tags={TAGS}
        includeTags={["bug"]}
        excludeTags={[]}
        matchMode="any"
        onToggle={() => {}}
        onMatchModeChange={() => {}}
      />,
    );
    expect(html).toContain(">OR<");
  });

  it("renders AND toggle when matchMode is all", () => {
    const html = renderToStaticMarkup(
      <TagFilterBar
        tags={TAGS}
        includeTags={["bug"]}
        excludeTags={[]}
        matchMode="all"
        onToggle={() => {}}
        onMatchModeChange={() => {}}
      />,
    );
    expect(html).toContain(">AND<");
  });

  it("includes tag has background color (highlighted)", () => {
    const html = renderToStaticMarkup(
      <TagFilterBar
        tags={TAGS}
        includeTags={["bug"]}
        excludeTags={[]}
        matchMode="any"
        onToggle={() => {}}
        onMatchModeChange={() => {}}
      />,
    );
    // The bug tag should have the red background color
    expect(html).toContain("#ef4444");
  });

  it("excluded tag has line-through textDecoration", () => {
    const html = renderToStaticMarkup(
      <TagFilterBar
        tags={TAGS}
        includeTags={[]}
        excludeTags={["bug"]}
        matchMode="any"
        onToggle={() => {}}
        onMatchModeChange={() => {}}
      />,
    );
    expect(html).toContain("line-through");
  });

  it("neutral tag has no background and no line-through", () => {
    const html = renderToStaticMarkup(
      <TagFilterBar
        tags={TAGS}
        includeTags={["bug"]}
        excludeTags={["urgent"]}
        matchMode="any"
        onToggle={() => {}}
        onMatchModeChange={() => {}}
      />,
    );
    // The "feature" tag should be in its default state (transparent background)
    expect(html).toContain("feature");
    // Should not have line-through for the feature tag
    // (we can't easily isolate which tag has line-through in SSR, but we can
    // check that not ALL tags have it)
    const lineThroughCount = (html.match(/line-through/g) || []).length;
    expect(lineThroughCount).toBe(1); // only "urgent" is excluded
  });
});
