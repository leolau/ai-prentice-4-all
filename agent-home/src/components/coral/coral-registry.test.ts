import { describe, expect, it } from "vitest";

// Phase 1 registrations run at import — the same moment they run in the app.
import "@/components/coral/coral-apps";
import {
  __resetCoralRegistryForTests,
  buildCoralLayout,
  clusterMemberAngles,
  getApps,
  isAppActive,
  petalAngle,
  petalPosition,
  registerApp,
  registerCluster,
} from "@/components/coral/coral-registry";
import type { AppManifest } from "@/components/coral/coral-types";

const app = (over: Partial<AppManifest> & { id: string }): AppManifest => ({
  name: over.id,
  glyph: "◦",
  kind: "next-route",
  route: `/${over.id}`,
  category: over.id,
  order: 0,
  ...over,
});

describe("coral Phase 1 registrations", () => {
  it("registers all 17 destinations with unique ids and routes", () => {
    const apps = getApps();
    expect(apps).toHaveLength(17);
    expect(new Set(apps.map((a) => a.id)).size).toBe(17);
    expect(new Set(apps.map((a) => a.route)).size).toBe(17);
  });

  it("builds 8 petals: 6 apps + 2 clusters, in order-band sequence", () => {
    const petals = buildCoralLayout();
    expect(petals).toHaveLength(8);
    expect(petals.map((p) => (p.type === "app" ? p.app.id : `cluster:${p.id}`))).toEqual([
      "home",
      "todos",
      "chat",
      "inbox",
      "memory",
      "projects",
      "cluster:workspace",
      "cluster:system",
    ]);
  });

  it("clusters hold the remaining 11 destinations", () => {
    const petals = buildCoralLayout();
    const workspace = petals.find((p) => p.type === "cluster" && p.id === "workspace");
    const system = petals.find((p) => p.type === "cluster" && p.id === "system");
    if (workspace?.type !== "cluster" || system?.type !== "cluster") {
      throw new Error("expected both clusters");
    }
    expect(workspace.members.map((m) => m.id)).toEqual([
      "files",
      "activity",
      "graph",
      "capacity",
    ]);
    expect(system.members.map((m) => m.id)).toEqual([
      "users",
      "suggestions",
      "tools",
      "core",
      "webview",
      "settings",
      "onboarding",
    ]);
  });

  it("keeps every registered app reachable from the layout exactly once", () => {
    const laid = buildCoralLayout();
    const ids = laid.flatMap((p) =>
      p.type === "app" ? [p.app.id] : p.members.map((m) => m.id),
    );
    expect(ids.sort()).toEqual(getApps().map((a) => a.id).sort());
  });

  it("keeps the badge-slot contract: exactly one todos-open slot", () => {
    const slots = getApps().filter((a) => a.badgeSlot === "todos-open");
    expect(slots.map((a) => a.id)).toEqual(["todos"]);
  });

  it("matches the old isActive semantics (root exact, others by prefix)", () => {
    expect(isAppActive("/", "/")).toBe(true);
    expect(isAppActive("/todos", "/")).toBe(false);
    expect(isAppActive("/todos/abc", "/todos")).toBe(true);
    expect(isAppActive("/tools", "/todos")).toBe(false);
  });
});

describe("coral caps and registration errors", () => {
  it("rejects a 9th top-level petal, naming the fix", () => {
    __resetCoralRegistryForTests();
    for (let i = 0; i < 9; i += 1) {
      registerApp(app({ id: `app-${i}`, order: i }));
    }
    expect(() => buildCoralLayout()).toThrow(/cap of 8.*cluster/);
  });

  it("rejects a 9th cluster member", () => {
    __resetCoralRegistryForTests();
    registerCluster("system", { label: "System", glyph: "❖" });
    for (let i = 0; i < 9; i += 1) {
      registerApp(app({ id: `s-${i}`, category: "system", order: i }));
    }
    expect(() => buildCoralLayout()).toThrow(/cap is 8/);
  });

  it("rejects two apps in one non-cluster category", () => {
    __resetCoralRegistryForTests();
    registerApp(app({ id: "one", category: "solo", order: 0 }));
    registerApp(app({ id: "two", category: "solo", route: "/two", order: 1 }));
    expect(() => buildCoralLayout()).toThrow(/not a cluster/);
  });

  it("rejects a cluster category with no registerCluster definition", () => {
    __resetCoralRegistryForTests();
    registerApp(app({ id: "orphan", category: "system", order: 0 }));
    expect(() => buildCoralLayout()).toThrow(/no registerCluster/);
  });

  it("rejects duplicate ids and routes at registration", () => {
    __resetCoralRegistryForTests();
    registerApp(app({ id: "dup" }));
    expect(() => registerApp(app({ id: "dup", route: "/other" }))).toThrow(/duplicate app id/);
    expect(() => registerApp(app({ id: "other", route: "/dup" }))).toThrow(/reuses route/);
  });

  it("rejects routes without a leading slash", () => {
    __resetCoralRegistryForTests();
    expect(() => registerApp(app({ id: "bad", route: "todos" }))).toThrow(/must start with/);
  });
});

describe("coral bloom geometry", () => {
  it("puts a single petal on the diagonal", () => {
    expect(petalAngle(0, 1)).toBe(135);
  });

  it("spreads petals evenly from straight-up to straight-left", () => {
    expect(petalAngle(0, 8)).toBe(90);
    expect(petalAngle(7, 8)).toBe(180);
    expect(petalAngle(3, 7)).toBeCloseTo(135, 5);
  });

  it("maps angles to screen offsets (up is negative y)", () => {
    const up = petalPosition(0, 2, 120);
    expect(up.x).toBeCloseTo(0, 1);
    expect(up.y).toBeCloseTo(-120, 1);
    const left = petalPosition(1, 2, 120);
    expect(left.x).toBeCloseTo(-120, 1);
    expect(left.y).toBeCloseTo(0, 1);
  });

  it("fans cluster members around the cluster angle, clamped on-screen", () => {
    const angles = clusterMemberAngles(170, 7);
    expect(angles).toHaveLength(7);
    for (const a of angles) {
      expect(a).toBeGreaterThanOrEqual(90);
      expect(a).toBeLessThanOrEqual(188);
    }
    // Ascending and evenly spaced once clamping stops biting.
    for (let i = 1; i < angles.length; i += 1) {
      expect(angles[i]).toBeGreaterThan(angles[i - 1] ?? 0);
    }
  });

  it("shifts the fan up when the cluster sits near straight-up", () => {
    const angles = clusterMemberAngles(95, 5);
    expect(angles[0]).toBe(90);
    expect(angles[angles.length - 1]).toBeLessThanOrEqual(195);
  });
});
