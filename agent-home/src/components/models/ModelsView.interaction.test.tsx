// @vitest-environment jsdom
/**
 * The pinned-cards section on ModelsView: lazy-fetches /api/models/pinned
 * after paint, renders one row per overriding card linking to its card
 * page, and stays hidden entirely when nothing is pinned.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModelsView } from "@/components/models/ModelsView";
import type { ModelsOverviewResponse } from "@/types";

const OVERVIEW: ModelsOverviewResponse = {
  info: {
    model: "glm-5.2",
    provider: "alibaba",
    auto_context_length: 1_048_576,
    config_context_length: 0,
    effective_context_length: 1_048_576,
    capabilities: {},
  },
  auxiliary: {
    main: { provider: "alibaba", model: "glm-5.2" },
    tasks: [{ task: "vision", provider: "auto", model: "", base_url: "" }],
  },
  usage: [],
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ModelsView pinned cards", () => {
  it("shows pinned cards with model, status and a link to the card", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        pinned: [
          {
            project_slug: "canva-deck",
            project_name: "Canva deck",
            task_id: "t_abc",
            title: "Generate slide 118",
            model: "deepseek-chat",
            status: "blocked",
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { findByText, container } = render(<ModelsView initial={OVERVIEW} />);
    await findByText("Generate slide 118");
    expect(container.textContent).toContain("Canva deck · blocked");
    expect(container.textContent).toContain("deepseek-chat");
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/projects/canva-deck/cards/t_abc");
  });

  it("renders no section when nothing is pinned", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { pinned: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<ModelsView initial={OVERVIEW} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.textContent).not.toContain("Pinned cards");
  });

  it("hides the section when the fetch fails — it is additive, not critical", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("down"));
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<ModelsView initial={OVERVIEW} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(container.textContent).not.toContain("Pinned cards"),
    );
  });
});
