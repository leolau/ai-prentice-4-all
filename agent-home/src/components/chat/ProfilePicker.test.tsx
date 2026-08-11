import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ProfilePicker } from "@/components/chat/ProfilePicker";
import type { ProfileSummary } from "@/types";

const PROFILES: ProfileSummary[] = [
  { name: "default", is_default: true, description: "" },
  { name: "maintenance", is_default: false, description: "Support & upkeep" },
];

describe("ProfilePicker", () => {
  it("renders nothing on a single-profile box", () => {
    const html = renderToStaticMarkup(
      <ProfilePicker
        profiles={[PROFILES[0]]}
        selected="default"
        onSelect={() => {}}
      />,
    );
    expect(html).toBe("");
  });

  it("offers every profile the box serves and marks the selected one", () => {
    const html = renderToStaticMarkup(
      <ProfilePicker
        profiles={PROFILES}
        selected="maintenance"
        onSelect={() => {}}
      />,
    );
    expect(html).toContain("maintenance");
    expect(html).toContain("default (default)");
    // The selected profile drives which brain answers, so it must be the
    // control's value rather than only a visual highlight.
    expect(html).toContain('value="maintenance"');
    expect(html).toContain("Support &amp; upkeep");
  });

  it("cannot be changed while a turn is in flight", () => {
    const html = renderToStaticMarkup(
      <ProfilePicker
        profiles={PROFILES}
        selected="default"
        onSelect={() => {}}
        disabled
      />,
    );
    expect(html).toContain("disabled");
  });

  it("reports the wait and holds itself while the profile loads", () => {
    const html = renderToStaticMarkup(
      <ProfilePicker
        profiles={PROFILES}
        selected="maintenance"
        onSelect={() => {}}
        switching
      />,
    );

    expect(html).toContain('role="status"');
    expect(html).toContain("animate-spin");
    expect(html).toContain("disabled");
    // The description gives way to the wait rather than sitting beside it.
    expect(html).not.toContain("Support &amp; upkeep");
  });
});
