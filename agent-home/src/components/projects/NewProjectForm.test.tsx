import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// The form navigates with next/navigation — stub the router hook so SSR
// rendering works.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: () => {}, push: () => {} }),
}));

import { NewProjectForm } from "@/components/projects/NewProjectForm";

describe("NewProjectForm", () => {
  it("step 1 asks for exactly the mandatory what-fields", () => {
    const html = renderToStaticMarkup(<NewProjectForm servingProfile="default" />);
    expect(html).toContain("Step 1 of 2");
    expect(html).toContain("Goal — what success means");
    expect(html).toContain("Description — the brief the agent works from");
    expect(html).toContain("Outputs — what it delivers");
    expect(html).toContain("Add another output");
    // Step 2 is not pre-rendered — one step at a time.
    expect(html).not.toContain("Step 2 of 2");
    expect(html).not.toContain("Host profile");
    // Step 1 advances, it does not create.
    expect(html).toContain("Next");
    expect(html).not.toContain("Create project");
  });
});
