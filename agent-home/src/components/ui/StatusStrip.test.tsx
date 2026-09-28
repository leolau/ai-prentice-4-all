// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StatusStrip } from "@/components/ui/StatusStrip";
import type { StatusSummary } from "@/types";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const SUMMARY: StatusSummary = {
  cpu_pct: 42,
  mem_pct: 61,
  disk_pct: 33,
  tokens_month: { input: 1_500_000, output: 250_000, cost_usd: 12.34 },
  active_conversations: 2,
  running_cards: 1,
  cron_total: 3,
  cron_enabled: 2,
  build: "abc1234",
  collected_at: 1,
};

function mockSummary(body: Partial<StatusSummary> | null = SUMMARY) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () =>
      new Response(body ? JSON.stringify(body) : "err", {
        status: body ? 200 : 500,
        headers: { "content-type": "application/json" },
      }),
    );
}

describe("StatusStrip", () => {
  it("shows the metric chips and the running build SHA", async () => {
    vi.stubEnv("NEXT_PUBLIC_HERMES_BUILD", "abc1234");
    mockSummary();
    render(<StatusStrip />);
    expect(await screen.findByText("42%")).toBeTruthy();
    expect(screen.getByText("33%")).toBeTruthy();
    // Tokens + cost render as separate text nodes inside the link.
    const tokenLink = screen.getByTitle(/Tokens used in the last 30 days/);
    expect(tokenLink.textContent).toContain("1.8M/mo");
    expect(tokenLink.textContent).toContain("$12.34");
    expect(screen.getByText("3")).toBeTruthy(); // tasks: 2 convos + 1 card
    expect(screen.getByText("2/3")).toBeTruthy(); // cron enabled/total
    // Same SHA on both ends → quiet build tag, no refresh prompt.
    expect(screen.getByTitle("agent-home build abc1234")).toBeTruthy();
    expect(screen.queryByText(/refresh/)).toBeNull();
    // The self-ticking freshness label.
    expect(await screen.findByText(/just now|\d+[smh] ago/)).toBeTruthy();
  });

  it("flags a stale bundle when the server reports a different build", async () => {
    vi.stubEnv("NEXT_PUBLIC_HERMES_BUILD", "old999");
    mockSummary({ ...SUMMARY, build: "abc1234" });
    render(<StatusStrip />);
    const btn = await screen.findByText("update abc1234 · refresh");
    expect(btn.getAttribute("title")).toContain("old999");
  });

  it("keeps the build tag up when the fetch fails", async () => {
    vi.stubEnv("NEXT_PUBLIC_HERMES_BUILD", "abc1234");
    mockSummary(null);
    render(<StatusStrip />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(screen.getByTitle("agent-home build abc1234")).toBeTruthy();
    // Metrics render as unknown ("—" on CPU, Disk, and Cron).
    expect(screen.getAllByText("—").length).toBe(3);
  });
});
