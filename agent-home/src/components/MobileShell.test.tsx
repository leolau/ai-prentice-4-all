import type { ComponentProps, ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MobileShell } from "@/components/MobileShell";

// Basic render test for the mobile shell (FG-20 Wave A). `showNav={false}`
// avoids the client-only BottomNav (usePathname) so this stays a pure
// server-render assertion, and skips the badge-count fetch entirely.
//
// The shell is an async server component (it awaits the To-dos badge count),
// and `renderToStaticMarkup` cannot render one: it suspends on synchronous
// input. Awaiting the component function and rendering the element it returns
// is the server-render equivalent, and keeps the assertions unchanged.
async function renderShell(
  props: ComponentProps<typeof MobileShell>,
): Promise<string> {
  return renderToStaticMarkup((await MobileShell(props)) as ReactElement);
}

describe("MobileShell", () => {
  it("renders the title, the data-component root, and safe-area padding", async () => {
    const html = await renderShell({
      title: "Sign in",
      showNav: false,
      children: <p>hello</p>,
    });
    expect(html).toContain('data-component="MobileShell"');
    expect(html).toContain("Sign in");
    expect(html).toContain("hello");
    // safe-area inset is wired for the notch/home-indicator.
    expect(html).toContain("safe-top");
  });

  it("carries the adaptive breakpoints so it widens past a phone column", async () => {
    const html = await renderShell({
      title: "Home",
      showNav: false,
      children: <p>panel</p>,
    });
    // Tablet widens the column; desktop switches to the sidebar flex layout.
    expect(html).toContain("md:max-w-2xl");
    expect(html).toContain("lg:flex");
    expect(html).toContain("lg:max-w-5xl");
  });

  it("renders optional actions in the header alongside the title", async () => {
    const html = await renderShell({
      title: "Chat",
      showNav: false,
      actions: <span>test-actions</span>,
      children: <p>panel</p>,
    });
    expect(html).toContain("Chat");
    expect(html).toContain("test-actions");
    // The header inner div uses justify-between so actions sit on the right.
    expect(html).toContain("justify-between");
  });
});
