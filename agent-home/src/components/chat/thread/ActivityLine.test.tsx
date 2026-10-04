// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ActivityLine } from "@/components/chat/thread/ActivityLine";

const sel = (c: Element, name: string) =>
  c.querySelector(`[data-component="${name}"]`);

describe("ActivityLine", () => {
  afterEach(cleanup);

  it("renders nothing for an idle turn with no steps", () => {
    const { container } = render(
      <ActivityLine activity="idle" tools={[]} reasoning="" hasOutput={false} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("shows a compact status with the running tool and a closed steps list", () => {
    const { container } = render(
      <ActivityLine
        activity="tool"
        tools={[
          { id: "a", name: "web_search", done: true },
          { id: "b", name: "read_file", done: true },
          { id: "c", name: "terminal", done: false },
        ]}
        reasoning="checking the logs"
        startedAt={Date.now()}
        lastEventAt={Date.now()}
        hasOutput={false}
      />,
    );
    const status = sel(container, "StatusIndicator")!;
    expect(status.getAttribute("data-compact")).toBe("true");
    expect(status.textContent).toContain("Running terminal…");
    const steps = sel(container, "LiveActivity") as HTMLDetailsElement;
    expect(steps.tagName).toBe("DETAILS");
    expect(steps.open).toBe(false);
    expect(steps.querySelector("summary")?.textContent).toBe("3 steps");
    expect(steps.textContent).toContain("web_search — done");
    expect(steps.textContent).toContain("terminal — running…");
    expect(sel(container, "LiveReasoning")?.textContent).toBe("checking the logs");
  });

  it("uses the singular for one step", () => {
    const { container } = render(
      <ActivityLine
        activity="tool"
        tools={[{ id: "a", name: "web_search", done: false }]}
        reasoning=""
        hasOutput={false}
      />,
    );
    expect(container.querySelector("summary")?.textContent).toBe("1 step");
    expect(sel(container, "LiveReasoning")).toBeNull();
  });

  it("keeps the steps once the turn is idle", () => {
    const { container } = render(
      <ActivityLine
        activity="idle"
        tools={[{ id: "a", name: "web_search", done: true }]}
        reasoning=""
        hasOutput
      />,
    );
    expect(sel(container, "StatusIndicator")).toBeNull();
    expect(sel(container, "LiveActivity")).not.toBeNull();
  });

  it("shows only the status before any step arrives", () => {
    const { container } = render(
      <ActivityLine activity="thinking" tools={[]} reasoning="" hasOutput={false} />,
    );
    expect(sel(container, "StatusIndicator")).not.toBeNull();
    expect(sel(container, "LiveActivity")).toBeNull();
  });
});
