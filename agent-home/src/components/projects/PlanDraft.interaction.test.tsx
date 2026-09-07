// @vitest-environment jsdom
/**
 * "Draft with the agent" in the Plan panel is a project-scoped, server-side
 * job: the button POSTs, the panel polls the same route, and the outcome is
 * reported in place — the user is never sent to chat, and the proposal only
 * ever lands as an inactive revision the lead activates.
 */
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const router = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

import { PlanPanel } from "@/components/projects/panels/PlanPanel";
import type { ProjectPlaybookDraftState } from "@/types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const PROPS = {
  slug: "monday-digest",
  playbook: { active: null, revisions: [] },
  profiles: ["default"],
  canActivate: true,
  archived: false,
};

const DRAFT_URL = "/api/projects/monday-digest/playbook/draft";

/** Answer GETs from a queue of states; POST starts the job. */
function draftServer(states: ProjectPlaybookDraftState[]) {
  const queue = [...states];
  return vi.fn((url: string, init?: RequestInit) => {
    if (url !== DRAFT_URL) return Promise.resolve(jsonResponse(404, {}));
    if (init?.method === "POST") {
      return Promise.resolve(jsonResponse(200, { status: "running" }));
    }
    const next = queue.length > 1 ? queue.shift()! : queue[0];
    return Promise.resolve(jsonResponse(200, next));
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  router.push.mockReset();
  router.refresh.mockReset();
});

describe("PlanPanel — Draft with the agent", () => {
  it("starts the server-side job, shows progress, then reports the proposal", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = draftServer([
      { status: "idle" },
      { status: "running", started_at: 1 },
      { status: "done", rev: 2 },
    ]);
    vi.stubGlobal("fetch", fetchMock);

    const { getByText, findByText, queryByText } = render(<PlanPanel {...PROPS} />);
    fireEvent.click(getByText("Draft with the agent"));

    await findByText("Agent is drafting…");
    expect(queryByText(/reading the brief/)).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe(DRAFT_URL);

    await vi.advanceTimersByTimeAsync(2_100);
    await findByText(/proposed revision 2/);
    expect(router.refresh).toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled();
    expect(queryByText("Agent is drafting…")).toBeNull();
  });

  it("explains a failed draft in place and re-enables the button", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = draftServer([
      { status: "idle" },
      { status: "failed", detail: "the agent did not return a plan" },
    ]);
    vi.stubGlobal("fetch", fetchMock);

    const { getByText, findByRole } = render(<PlanPanel {...PROPS} />);
    fireEvent.click(getByText("Draft with the agent"));

    await vi.advanceTimersByTimeAsync(2_100);
    const alert = await findByRole("alert");
    expect(alert.textContent).toMatch(/could not draft|did not return/);
    expect((getByText("Draft with the agent") as HTMLButtonElement).disabled).toBe(false);
  });

  it("resumes waiting on a draft that was running before the page loaded", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = draftServer([{ status: "running", started_at: 1 }]);
    vi.stubGlobal("fetch", fetchMock);

    const { findByText } = render(<PlanPanel {...PROPS} />);
    await findByText("Agent is drafting…");
    expect(fetchMock.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(true);
  });

  it("refuses a second draft while one is running, without leaving the page", async () => {
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Promise.resolve(
          jsonResponse(409, { detail: "a draft is already running for this project" }),
        );
      }
      return Promise.resolve(jsonResponse(200, { status: "idle" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { getByText, findByRole } = render(<PlanPanel {...PROPS} />);
    fireEvent.click(getByText("Draft with the agent"));
    const alert = await findByRole("alert");
    expect(alert.textContent).toBeTruthy();
    expect(router.push).not.toHaveBeenCalled();
  });
});
