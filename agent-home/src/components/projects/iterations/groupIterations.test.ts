import { describe, expect, it } from "vitest";

import { groupIterations } from "@/components/projects/iterations/groupIterations";
import type {
  PlaybookRev,
  ProjectChange,
  ProjectDelivery,
  ProjectDirective,
  ProjectHistoryRun,
  ProjectOutputWithDeliveries,
  ProjectRunBrief,
} from "@/types";

const T = 1_700_000_000;
const H = 3600;

const rev = (n: number, activated_at: number | null, active = 0): PlaybookRev => ({
  project_id: "p",
  rev: n,
  body: "",
  steps: [],
  active,
  created_by: "leo",
  created_at: (activated_at ?? T) - 10,
  activated_at,
  note: null,
});

const dir = (id: string, created_at: number, over: Partial<ProjectDirective> = {}): ProjectDirective => ({
  id,
  project_id: "p",
  kind: "directive",
  body: `req ${id}`,
  scope: "project",
  target_ref: null,
  rating: null,
  author_user_id: "leo",
  created_at,
  active: 1,
  retired_at: null,
  superseded_by: null,
  ...over,
});

const run = (run_no: number, started_at: number, over: Partial<ProjectRunBrief> = {}): ProjectRunBrief => ({
  run_no,
  status: "done",
  trigger: "manual",
  started_at,
  ended_at: started_at + H,
  duration_seconds: H,
  outcome: "delivered",
  score_user: null,
  ...over,
});

const delivery = (id: string, delivered_at: number, run_id: string | null = null): ProjectDelivery => ({
  id,
  output_id: "o1",
  run_id,
  task_id: null,
  link_kind: null,
  link_ref: null,
  profile: null,
  label: id,
  note: null,
  delivered_at,
});

const output = (deliveries: ProjectDelivery[]): ProjectOutputWithDeliveries =>
  ({ id: "o1", title: "4 MOUs", deliveries }) as unknown as ProjectOutputWithDeliveries;

const dirs = (list: ProjectDirective[]) => ({ directives: list, applies_from: "next run" });
const book = (revisions: PlaybookRev[]) => ({
  active: revisions.find((r) => r.active) ?? null,
  revisions,
});

describe("groupIterations", () => {
  it("no runs: one upcoming iteration carrying the current requirements", () => {
    const h = groupIterations(
      { runs: [], outputs: [] },
      dirs([dir("r1", T)]),
      book([rev(1, T, 1)]),
    );
    expect(h.iterations).toHaveLength(1);
    const [it] = h.iterations;
    expect(it).toMatchObject({ no: 1, status: "upcoming", planRev: 1, requirementIds: ["r1"], current: true });
    expect(it.added.map((r) => r.id)).toEqual(["r1"]);
    expect(h.startedCount).toBe(0);
    expect(h.nextIterationNo).toBe(1);
    expect(h.openRun).toBeNull();
    expect(h.requirements[0]).toMatchObject({ id: "r1", status: "active", firstIteration: 1 });
  });

  it("no runs, no plan, no directives still yields iteration 1", () => {
    const h = groupIterations({ runs: [], outputs: [] }, null, null);
    expect(h.iterations.map((i) => [i.no, i.status])).toEqual([[1, "upcoming"]]);
  });

  it("one run: one started iteration with its deliveries, nothing upcoming", () => {
    const h = groupIterations(
      { runs: [run(1, T + H)], outputs: [output([delivery("d1", T + H + 60), delivery("late", T + 9 * H)])] },
      dirs([dir("r1", T)]),
      book([rev(1, T, 1)]),
    );
    expect(h.iterations).toHaveLength(1);
    const [it] = h.iterations;
    expect(it).toMatchObject({ no: 1, status: "done", planRev: 1, current: true });
    expect(it.runs.map((r) => r.run_no)).toEqual([1]);
    expect(it.deliveries.map((d) => d.delivery.id)).toEqual(["d1"]);
    expect(it.deliveries[0].outputTitle).toBe("4 MOUs");
    expect(h.nextIterationNo).toBe(2);
  });

  it("an open run is the current iteration and is reported", () => {
    const h = groupIterations(
      { runs: [run(1, T + H, { status: "running", ended_at: null })], outputs: [] },
      dirs([]),
      book([rev(1, T, 1)]),
    );
    expect(h.openRun?.run_no).toBe(1);
    expect(h.iterations[0]).toMatchObject({ status: "running", current: true });
  });

  it("several plan revisions split iterations; repeat runs on one revision share one", () => {
    const h = groupIterations(
      { runs: [run(1, T + H), run(2, T + 3 * H), run(3, T + 6 * H)], outputs: [] },
      dirs([]),
      book([rev(1, T), rev(2, T + 5 * H, 1)]),
    );
    expect(h.iterations.map((i) => [i.no, i.planRev, i.runs.map((r) => r.run_no)])).toEqual([
      [2, 2, [3]],
      [1, 1, [1, 2]],
    ]);
    expect(h.iterations[0].current).toBe(true);
    expect(h.iterations[1].current).toBe(false);
  });

  it("an activated-but-unrun plan leads as upcoming", () => {
    const h = groupIterations(
      { runs: [run(1, T + H)], outputs: [] },
      dirs([]),
      book([rev(1, T), rev(2, T + 5 * H, 1)]),
    );
    expect(h.iterations.map((i) => [i.no, i.status, i.planRev])).toEqual([
      [2, "upcoming", 2],
      [1, "done", 1],
    ]);
    expect(h.iterations[1].current).toBe(true);
  });

  it("directives added between runs start a new iteration and say what was added", () => {
    const h = groupIterations(
      { runs: [run(1, T + H), run(2, T + 4 * H)], outputs: [] },
      dirs([dir("r1", T), dir("r2", T + 2 * H)]),
      book([rev(1, T, 1)]),
    );
    expect(h.iterations.map((i) => i.requirementIds)).toEqual([["r1", "r2"], ["r1"]]);
    expect(h.iterations[0].added.map((r) => r.id)).toEqual(["r2"]);
    const byId = Object.fromEntries(h.requirements.map((r) => [r.id, r]));
    expect(byId.r1.firstIteration).toBe(1);
    expect(byId.r2.firstIteration).toBe(2);
    expect(h.requirements.map((r) => r.id)).toEqual(["r2", "r1"]);
  });

  it("a directive added after the last run applies to the upcoming iteration", () => {
    const h = groupIterations(
      { runs: [run(1, T + H)], outputs: [] },
      dirs([dir("r1", T + 3 * H)]),
      book([rev(1, T, 1)]),
    );
    expect(h.iterations[0]).toMatchObject({ no: 2, status: "upcoming" });
    expect(h.requirements[0].firstIteration).toBe(2);
  });

  it("retired directives drop out of later iterations and are listed as retired", () => {
    const history = {
      changes: [
        { ...dir("r1", T, { active: 0, retired_at: T + 2 * H, superseded_by: "r2" }), kinds: [] },
        { ...dir("r2", T + 2 * H), kinds: ["direction"] },
        { ...dir("fb", T + 2 * H, { kind: "feedback" }), kinds: [] },
        { ...dir("prop", T + 2 * H, { active: 0 }), kinds: [] },
      ] as unknown as ProjectChange[],
      runs: [
        { ...run(1, T + H), id: "run_1", playbook_rev: 1, deliveries: 0, cards_done: 1, cards_total: 1 },
        { ...run(2, T + 4 * H), id: "run_2", playbook_rev: 1, deliveries: 0, cards_done: 1, cards_total: 1 },
      ] as ProjectHistoryRun[],
    };
    const h = groupIterations(
      { runs: [], outputs: [] },
      null,
      book([rev(1, T, 1)]),
      history,
    );
    expect(h.iterations.map((i) => i.requirementIds)).toEqual([["r2"], ["r1"]]);
    expect(h.iterations[0].retired.map((r) => r.id)).toEqual(["r1"]);
    const byId = Object.fromEntries(h.requirements.map((r) => [r.id, r]));
    expect(byId.r1).toMatchObject({ status: "retired", supersededBy: "r2", firstIteration: 1 });
    expect(byId.r2).toMatchObject({ status: "active", kinds: ["direction"], firstIteration: 2 });
    expect(byId.prop).toMatchObject({ status: "proposed", firstIteration: null });
    expect(byId.fb).toBeUndefined();
  });

  it("history runs carry the pinned revision and claim deliveries by run id", () => {
    const history = {
      changes: [],
      runs: [
        { ...run(1, T + H), id: "run_a", playbook_rev: 3, deliveries: 1, cards_done: 2, cards_total: 3 },
        { ...run(2, T + 2 * H, { ended_at: null, status: "waiting" }), id: "run_b", playbook_rev: 3, deliveries: 0, cards_done: 0, cards_total: 3 },
      ] as ProjectHistoryRun[],
    };
    const h = groupIterations(
      { runs: [], outputs: [output([delivery("d1", T + 5 * H, "run_a")])] },
      null,
      book([rev(3, T, 1)]),
      history,
    );
    expect(h.iterations).toHaveLength(1);
    const [it] = h.iterations;
    expect(it.planRev).toBe(3);
    expect(it.runs.map((r) => r.run_no)).toEqual([1, 2]);
    expect(it.runs[0].deliveries.map((d) => d.delivery.id)).toEqual(["d1"]);
    expect(it.runs[0].cardsDone).toBe(2);
    expect(h.openRun?.run_no).toBe(2);
  });
});
