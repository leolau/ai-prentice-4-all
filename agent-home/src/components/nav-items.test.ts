import { describe, expect, it } from "vitest";

import { isActive, PRIMARY_NAV, SECONDARY_NAV } from "@/components/nav-items";

// The mobile BottomNav and desktop SideNav share this model, so the active-tab
// rule must match exactly and the two surfaces must cover the same routes.
describe("nav-items", () => {
  it("matches the root tab only on an exact path", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/graph", "/")).toBe(false);
  });

  it("matches non-root tabs on a path prefix (nested routes stay active)", () => {
    expect(isActive("/activity", "/activity")).toBe(true);
    expect(isActive("/activity/abc123", "/activity")).toBe(true);
    expect(isActive("/inbox", "/activity")).toBe(false);
  });

  it("keeps primary and secondary destinations distinct", () => {
    const primary = new Set(PRIMARY_NAV.map((i) => i.href));
    for (const item of SECONDARY_NAV) {
      expect(primary.has(item.href)).toBe(false);
    }
    expect(PRIMARY_NAV.map((i) => i.href)).toContain("/");
  });

  // FG-23: Memory is a primary tab (replaced Activity); Activity moved to
  // secondary. Five is the bottom-bar budget on a phone.
  it("has Memory in primary nav and Activity in secondary", () => {
    const primaryHrefs = PRIMARY_NAV.map((i) => i.href);
    const secondaryHrefs = SECONDARY_NAV.map((i) => i.href);
    expect(primaryHrefs).toContain("/memory");
    expect(secondaryHrefs).toContain("/activity");
    expect(primaryHrefs).not.toContain("/activity");
    expect(secondaryHrefs).not.toContain("/memory");
  });

  // To-dos took Graph's primary slot: the to-do list is where the user is
  // asked for something, so it has to be one tap away.
  it("has To-dos in primary nav and Graph in secondary", () => {
    const primaryHrefs = PRIMARY_NAV.map((i) => i.href);
    const secondaryHrefs = SECONDARY_NAV.map((i) => i.href);
    expect(primaryHrefs).toContain("/todos");
    expect(secondaryHrefs).toContain("/graph");
    expect(primaryHrefs).not.toContain("/graph");
  });

  it("keeps primary nav at or below five tabs", () => {
    expect(PRIMARY_NAV.length).toBeLessThanOrEqual(5);
  });

  // The mobile MoreSheet (opened from the 6th BottomNav tab) surfaces every
  // SECONDARY_NAV item — so all of them must be reachable on a phone.
  it("includes Files, Activity, and Settings in secondary nav", () => {
    const secondaryHrefs = SECONDARY_NAV.map((i) => i.href);
    expect(secondaryHrefs).toContain("/files");
    expect(secondaryHrefs).toContain("/activity");
    expect(secondaryHrefs).toContain("/settings");
  });
});
