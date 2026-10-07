// @vitest-environment jsdom
import { cleanup, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CardModelCalls } from "@/components/projects/CardModelCalls";
import type { CardModelCalls as Data } from "@/types";

afterEach(cleanup);

const DATA: Data = {
  main: { calls: 117, failures: 1, min_ms: 2100, avg_ms: 26100, max_ms: 316400 },
  rows: [
    {
      run_id: 218, attempt: 2, run_outcome: "completed", caller: "main",
      model: "deepseek-v4.1-flash", provider: "opencode-go",
      calls: 87, failures: 1, min_ms: 3200, avg_ms: 32500, max_ms: 316400,
    },
    {
      run_id: 218, attempt: 2, run_outcome: "completed", caller: "aux:vision",
      model: "qwen3.8-flash", provider: "opencode-go",
      calls: 5, failures: 3, min_ms: 28000, avg_ms: 90000, max_ms: 240000,
    },
    {
      run_id: 217, attempt: 1, run_outcome: "crashed", caller: "main",
      model: "deepseek-v4.1-flash", provider: "opencode-go",
      calls: 30, failures: 0, min_ms: 2100, avg_ms: 7400, max_ms: 54300,
    },
  ],
};

describe("CardModelCalls", () => {
  it("shows main-model totals and one row per attempt, caller and model", () => {
    const { container } = render(<CardModelCalls data={DATA} />);
    const box = within(container.querySelector('[data-component="CardModelCalls"]') as HTMLElement);
    expect(box.getByText("117")).toBeTruthy();
    expect(box.getByText("26.1s")).toBeTruthy();
    expect(box.getAllByText("5m 16s").length).toBe(2); // total max + attempt 2 max
    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain("Attempt 2 · completed");
    expect(rows[0].textContent).toContain("main model");
    expect(rows[1].textContent).toContain("image checks");
    expect(rows[1].textContent).toContain("4m 0s");
    expect(rows[2].textContent).toContain("Attempt 1 · crashed");
    expect(rows[2].textContent).toContain("54.3s");
  });

  it("says so when no calls are recorded yet", () => {
    const { container } = render(<CardModelCalls data={null} />);
    expect(container.textContent).toContain("No model calls recorded for this card yet.");
    expect(container.querySelector("table")).toBeNull();
  });
});
