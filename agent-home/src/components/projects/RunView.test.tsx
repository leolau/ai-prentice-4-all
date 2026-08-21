// @vitest-environment jsdom
/**
 * Archived gating on the run page (U7 UI): a shelved project's run keeps
 * its record visible but hides every growing write — Continue, Repeat
 * this run, Save retro, the score control — while Cancel (a reducing act)
 * stays, matching what the router refuses and permits.
 */
import { cleanup, render } from "@testing-library/react";
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
