// @vitest-environment jsdom
/**
 * Archived gating on the run page (U7 UI): a shelved project's run keeps
 * its record visible but hides every growing write — Continue, Repeat
 * this run, Save retro, the score control — while Cancel (a reducing act)
 * stays, matching what the router refuses and permits.
 */
import {
  cleanup,
  fireEvent,
  render,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const router = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

import { RunView } from "@/components/projects/RunView";
import type { ProjectRun } from "@/types";

const NOW = Math.floor(Date.now() / 1000);

const RUN = (over: Partial<ProjectRun>): ProjectRun => ({
  id: "run_1",
  project_id: "prj_1",
  run_no: 1,
  trigger: "manual",
  triggered_by: "leo",
  profile: "default",
  playbook_rev: 1,
  status: "waiting",
  started_at: NOW - 3600,
  ended_at: null,
  session_id: null,
  trace_id: null,
  outcome: null,
  summary: null,
  retro: null,
  retro_at: null,
  score_self: null,
  score_user: null,
  score_note: null,
  scored_by: null,
  scored_at: null,
  error: null,
  cards: [],
  deliveries: [],
  ...over,
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("RunView on an archived project", () => {
  it("hides the growing writes, keeps Cancel, shows the hint", () => {
    const { queryByRole, queryByLabelText, getByText } = render(
      <RunView slug="monday-digest" run={RUN({})} archived />,
    );
    expect(queryByRole("button", { name: "Continue" })).toBeNull();
    expect(queryByRole("button", { name: "Save retro" })).toBeNull();
    expect(queryByLabelText("Score this run from 1 to 5")).toBeNull();
    expect(queryByRole("button", { name: "Repeat this run" })).toBeNull();
    // Cancel is the sanctioned way out — it stays.
    expect(queryByRole("button", { name: "Cancel" })).not.toBeNull();
    expect(getByText(/This project is archived/)).toBeTruthy();
  });

  it("still offers every write on a live project", () => {
    const { getByRole, getByLabelText, queryByText } = render(
      <RunView slug="monday-digest" run={RUN({})} />,
    );
    expect(getByRole("button", { name: "Continue" })).toBeTruthy();
    expect(getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(getByRole("button", { name: "Save retro" })).toBeTruthy();
    expect(getByLabelText("Score this run from 1 to 5")).toBeTruthy();
    expect(queryByText(/This project is archived/)).toBeNull();
  });

  it("hides Repeat this run on a finished archived run", () => {
    const finished = RUN({ status: "done", ended_at: NOW });
    const archivedView = render(
      <RunView slug="monday-digest" run={finished} archived />,
    );
    expect(
      archivedView.queryByRole("button", { name: "Repeat this run" }),
    ).toBeNull();
    archivedView.unmount();

    const liveView = render(<RunView slug="monday-digest" run={finished} />);
    expect(
      liveView.getByRole("button", { name: "Repeat this run" }),
    ).toBeTruthy();
  });
});

describe("RunView stall truth", () => {
  it("flags a stalled running run and offers stop + restart + retry", () => {
    const stalledRun = RUN({
      status: "running",
      stalled: true,
      cards: [
        { task_id: "t_1", step_key: "draft", status: "todo", title: "Draft" },
      ],
      blocked_tasks: [
        {
          task_id: "t_9",
          title: "Extract objectives",
          status: "blocked",
          error: "worker exited cleanly (rc=0) — protocol violation",
        },
      ],
    });
    const { getByText, getByRole } = render(
      <RunView slug="monday-digest" run={stalledRun} />,
    );
    expect(getByText("stalled")).toBeTruthy();
    expect(getByText(/no worker is active/)).toBeTruthy();
    // Stop and restart offered together on a stalled run.
    expect(getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(getByRole("button", { name: "Repeat this run" })).toBeTruthy();
    // The blocked work is listed with why it stopped.
    expect(getByText("Extract objectives")).toBeTruthy();
    expect(getByText(/protocol violation/)).toBeTruthy();
  });

  it("shows no stall UI on a healthy running run", () => {
    const healthy = RUN({
      status: "running",
      stalled: false,
      cards: [
        {
          task_id: "t_1",
          step_key: "draft",
          status: "running",
          title: "Draft",
        },
      ],
    });
    const { queryByText, queryByRole } = render(
      <RunView slug="monday-digest" run={healthy} />,
    );
    expect(queryByText("stalled")).toBeNull();
    expect(queryByText(/no worker is active/)).toBeNull();
    expect(queryByRole("button", { name: "Repeat this run" })).toBeNull();
  });
});

describe("RunView Stop now", () => {
  it("asks first, then posts to the stop verb — not cancel", async () => {
    const fetchMock = vi.fn(
      async (url: string) =>
        ({
          ok: true,
          json: async () => ({ status: "cancelled", outcome: "stopped" }),
          url,
        }) as unknown as Response,
    );
    vi.stubGlobal("fetch", fetchMock);
    const confirmMock = vi.fn(() => false);
    vi.stubGlobal("confirm", confirmMock);

    const { getByRole } = render(
      <RunView slug="monday-digest" run={RUN({ status: "running" })} />,
    );
    const stop = getByRole("button", { name: "Stop now" });
    const stopCalls = () =>
      fetchMock.mock.calls.filter(
        (call) => String(call[0]) === "/api/projects/monday-digest/runs/1/stop",
      );

    // Declined: nothing is stopped. A kill must not happen on one stray tap.
    fireEvent.click(stop);
    expect(confirmMock).toHaveBeenCalled();
    expect(stopCalls()).toHaveLength(0);

    confirmMock.mockReturnValue(true);
    fireEvent.click(stop);
    await waitFor(() => expect(stopCalls()).toHaveLength(1));
  });

  it("is not offered once the run is over", () => {
    const { queryByRole } = render(
      <RunView slug="monday-digest" run={RUN({ status: "done" })} />,
    );
    expect(queryByRole("button", { name: "Stop now" })).toBeNull();
  });
});
