// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

// CoralHost uses usePathname — mock to a neutral path so no petal is active.
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
}));
// NavGlyph's useLinkStatus only works inside the App Router's link context;
// in unit tests the petal just shows its glyph.
vi.mock("next/link", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next/link")>();
  return {
    ...actual,
    useLinkStatus: () => ({ pending: false }),
  };
});

import { CoralHost } from "@/components/coral/CoralHost";
import { buildCoralLayout } from "@/components/coral/coral-registry";

const openCoral = () => fireEvent.click(screen.getByRole("button", { name: /open coral menu/i }));

describe("CoralHost (SSR)", () => {
  it("renders the FAB closed — no petals in server markup", () => {
    const html = renderToStaticMarkup(<CoralHost />);
    expect(html).toContain('data-component="CoralHost"');
    expect(html).toContain("Open Coral menu");
    expect(html).not.toContain('role="menu"');
    expect(html).not.toContain('aria-expanded="true"');
  });
});

describe("CoralHost bloom", () => {
  it("opens on tap and shows every top-level petal", () => {
    render(<CoralHost />);
    openCoral();
    const menu = screen.getByRole("menu", { name: /coral launcher/i });
    expect(menu).toBeTruthy();
    // 6 app petals + 2 cluster buttons.
    for (const label of ["Home", "To-dos", "Chat", "Inbox", "Memory", "Projects", "Workspace", "System"]) {
      expect(menu.textContent).toContain(label);
    }
    expect(screen.getByRole("button", { name: /close coral menu/i })).toBeTruthy();
  });

  it("marks the active route with aria-current", () => {
    render(<CoralHost />);
    openCoral();
    const home = screen.getByRole("menuitem", { name: /home/i });
    expect(home.getAttribute("aria-current")).toBe("page");
  });

  it("closes on backdrop click and returns focus to the FAB", () => {
    render(<CoralHost />);
    const fab = screen.getByRole("button", { name: /open coral menu/i });
    fireEvent.click(fab);
    expect(screen.queryByRole("menu")).toBeTruthy();
    fireEvent.click(document.querySelector(".coral-backdrop") as Element);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(fab);
  });

  it("closes on Escape", () => {
    render(<CoralHost />);
    openCoral();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("fans a cluster's members and Escape closes the fan before the bloom", () => {
    render(<CoralHost />);
    openCoral();
    const workspace = screen.getByRole("menuitem", { name: /workspace/i });
    fireEvent.click(workspace);
    // Cluster members appear.
    for (const label of ["Files", "Activity", "Graph", "Capacity"]) {
      expect(screen.getByRole("menu", { name: /coral launcher/i }).textContent).toContain(label);
    }
    // First Escape closes the fan only; the bloom stays.
    fireEvent.keyDown(window, { key: "Escape" });
    const menu = screen.getByRole("menu", { name: /coral launcher/i });
    expect(menu.textContent).not.toContain("Files");
    expect(menu.textContent).toContain("Workspace");
    // Second Escape closes the bloom.
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("renders the todos badge count on the Tasks petal", () => {
    render(<CoralHost badgeCounts={{ "todos-open": 4 }} />);
    openCoral();
    expect(screen.getByRole("menu", { name: /coral launcher/i }).textContent).toContain("4");
  });

  it("covers every registered destination through petals + cluster fans", () => {
    render(<CoralHost />);
    openCoral();
    const menu = screen.getByRole("menu", { name: /coral launcher/i });
    for (const petal of buildCoralLayout()) {
      if (petal.type === "app") {
        expect(menu.textContent).toContain(petal.app.name);
      } else {
        fireEvent.click(screen.getByRole("menuitem", { name: petal.label }));
        for (const member of petal.members) {
          expect(menu.textContent).toContain(member.name);
        }
        fireEvent.click(screen.getByRole("menuitem", { name: petal.label }));
      }
    }
  });
});
