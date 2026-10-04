import { describe, expect, it } from "vitest";

import {
  ALL_STAGES,
  DONE_VISIBLE,
  LANES,
  RUN_STALL_SECONDS,
  assigneeAvatar,
  boardTasks,
  canActOnBoard,
  filterCounts,
  filterTasks,
  groupIntoLanes,
  isRunStalled,
  laneEmpty,
  laneForStatus,
  needsYou,
  openRunState,
  reconcileTask,
  runNumbers,
  shortAge,
  splitDone,
  withAssignee,
  withStatus,
  type FilterContext,
} from "@/components/projects/board/model";
import type { ProjectBoardContext, ProjectBoardTask, ProjectBoardView, ProjectDetail } from "@/types";

const NOW = 1_800_000_000;

function task(id: string, status: string, over: Partial<ProjectBoardTask> = {}): ProjectBoardTask {
  return {
    id,
    title: `Card ${id}`,
    body: null,
    status,
    assignee: null,
    priority: 0,
    created_at: NOW - 3600,
    started_at: null,
    completed_at: null,
    tenant: null,
    project_id: "prj_1",
    result: null,
    current_step_key: null,
    ...over,
  };
}

function board(...tasks: ProjectBoardTask[]): ProjectBoardView {
  return {
    columns: ALL_STAGES.map((name) => ({ name, tasks: tasks.filter((t) => t.status === name) })),
  };
}

const CTX: FilterContext = {
  userId: "leo",
  canAct: true,
  cardRuns: { run_card: 2, old_run_card: 1 },
  memberIds: new Set(["leo", "yan"]),
};

describe("laneForStatus", () => {
  it.each([
    ["triage", "up_next"],
    ["todo", "up_next"],
    ["scheduled", "up_next"],
    ["ready", "up_next"],
    ["running", "working"],
    ["blocked", "waiting"],
    ["review", "waiting"],
    ["done", "done"],
    ["archived", null],
  ])("%s → %s", (status, lane) => {
    expect(laneForStatus(status)).toBe(lane);
  });

  it("covers every stage exactly once across the four lanes", () => {
    const folded = LANES.flatMap((l) => l.statuses);
    expect([...folded].sort()).toEqual([...ALL_STAGES].sort());
    for (const lane of LANES) {
      for (const s of lane.statuses) expect(laneForStatus(s)).toBe(lane.key);
    }
  });

  it("keeps an unknown stage on the board rather than dropping it", () => {
    expect(laneForStatus("someday")).toBe("up_next");
  });
});

describe("boardTasks / groupIntoLanes", () => {
  it("drops archived cards and duplicates", () => {
    const b: ProjectBoardView = {
      columns: [
        { name: "ready", tasks: [task("a", "ready")] },
        { name: "archived", tasks: [task("z", "archived")] },
        { name: "done", tasks: [task("a", "ready")] },
      ],
    };
    expect(boardTasks(b).map((t) => t.id)).toEqual(["a"]);
    expect(boardTasks(null)).toEqual([]);
  });

  it("orders each lane the way a person reads it", () => {
    const lanes = groupIntoLanes([
      task("tri", "triage", { created_at: NOW - 10 }),
      task("rdy_low", "ready", { priority: 0, created_at: NOW - 100 }),
      task("rdy_hi", "ready", { priority: 5, created_at: NOW - 5 }),
      task("todo", "todo"),
      task("run_new", "running", { started_at: NOW - 10 }),
      task("run_old", "running", { started_at: NOW - 500 }),
      task("rev", "review", { created_at: NOW - 9000 }),
      task("blk", "blocked", { created_at: NOW - 10 }),
      task("done_old", "done", { completed_at: NOW - 900 }),
      task("done_new", "done", { completed_at: NOW - 10 }),
      task("arch", "archived"),
    ]);
    expect(lanes.up_next.map((t) => t.id)).toEqual(["rdy_hi", "rdy_low", "todo", "tri"]);
    expect(lanes.working.map((t) => t.id)).toEqual(["run_old", "run_new"]);
    expect(lanes.waiting.map((t) => t.id)).toEqual(["blk", "rev"]);
    expect(lanes.done.map((t) => t.id)).toEqual(["done_new", "done_old"]);
  });

  it("shows the latest few done cards and counts the rest", () => {
    const done = Array.from({ length: DONE_VISIBLE + 4 }, (_, i) => task(`d${i}`, "done"));
    expect(splitDone(done, false)).toEqual({ shown: done.slice(0, DONE_VISIBLE), more: 4 });
    expect(splitDone(done, true)).toEqual({ shown: done, more: 0 });
    expect(splitDone(done.slice(0, 2), false).more).toBe(0);
  });
});

describe("needsYou", () => {
  it("picks triage, blocked and review cards only", () => {
    const items = needsYou(
      ALL_STAGES.map((s) => task(s, s)).concat(task("arch", "archived")),
    );
    expect(items.map((i) => [i.task.id, i.kind])).toEqual([
      ["blocked", "unblock"],
      ["review", "review"],
      ["triage", "approve"],
    ]);
  });

  it("orders blocked (questions first) → review → triage, oldest first", () => {
    const items = needsYou([
      task("tri_new", "triage", { created_at: NOW - 1 }),
      task("tri_old", "triage", { created_at: NOW - 999 }),
      task("blk_plain", "blocked", { created_at: NOW - 999, block_kind: "transient" }),
      task("blk_ask", "blocked", { created_at: NOW - 1, block_kind: "needs_input" }),
      task("rev", "review"),
    ]);
    expect(items.map((i) => i.task.id)).toEqual([
      "blk_ask",
      "blk_plain",
      "rev",
      "tri_old",
      "tri_new",
    ]);
  });
});

describe("filters", () => {
  const tasks = [
    task("mine_hand", "triage", { created_by: "leo" }),
    task("mine_prefixed", "ready", { created_by: "user:leo" }),
    task("run_card", "blocked", { created_by: "leo" }),
    task("yan_card", "review", { created_by: "yan" }),
    task("agent_assigned", "running", { created_by: "yan", assignee: "default" }),
    task("scheduler", "todo", { created_by: "projects" }),
    task("assigned_me", "done", { assignee: "leo", created_by: "yan" }),
  ];

  it("Mine is cards I made by hand or that are assigned to me", () => {
    expect(filterTasks(tasks, "mine", CTX).map((t) => t.id)).toEqual([
      "mine_hand",
      "mine_prefixed",
      "assigned_me",
    ]);
  });

  it("Agent is run-made, agent-assigned or agent-created cards", () => {
    expect(filterTasks(tasks, "agent", CTX).map((t) => t.id)).toEqual([
      "run_card",
      "agent_assigned",
      "scheduler",
    ]);
  });

  it("Needs me is the needs-you set when I may act, empty for a viewer", () => {
    expect(filterTasks(tasks, "needs_me", CTX).map((t) => t.id)).toEqual([
      "mine_hand",
      "run_card",
      "yan_card",
    ]);
    expect(filterTasks(tasks, "needs_me", { ...CTX, canAct: false })).toEqual([]);
  });

  it("All keeps everything; a run number narrows any filter", () => {
    expect(filterTasks(tasks, "all", CTX)).toHaveLength(tasks.length);
    expect(filterTasks(tasks, "all", CTX, 2).map((t) => t.id)).toEqual(["run_card"]);
    expect(filterTasks(tasks, "mine", CTX, 2)).toEqual([]);
  });

  it("counts agree with the filters", () => {
    const counts = filterCounts(tasks, CTX);
    for (const f of ["all", "needs_me", "mine", "agent"] as const) {
      expect(counts[f]).toBe(filterTasks(tasks, f, CTX).length);
    }
  });

  it("lists the runs the visible cards came from, newest first", () => {
    expect(runNumbers([...tasks, task("old_run_card", "done")], CTX.cardRuns)).toEqual([2, 1]);
  });
});

describe("canActOnBoard", () => {
  const project = {
    archived: false,
    owner_user_id: "owner",
    members: [
      { project_id: "p", user_id: "mem", role: "member", added_by: null, added_at: 0 },
      { project_id: "p", user_id: "vic", role: "viewer", added_by: null, added_at: 0 },
    ],
  } as Pick<ProjectDetail, "archived" | "owner_user_id" | "members">;

  it("members, leads and the owner act; viewers and strangers don't", () => {
    expect(canActOnBoard(project, "mem", false)).toBe(true);
    expect(canActOnBoard(project, "owner", false)).toBe(true);
    expect(canActOnBoard(project, "someone", true)).toBe(true);
    expect(canActOnBoard(project, "vic", false)).toBe(false);
    expect(canActOnBoard(project, "eve", false)).toBe(false);
    expect(canActOnBoard({ ...project, archived: true }, "owner", true)).toBe(false);
  });
});

describe("assigneeAvatar / shortAge", () => {
  it("tells a person from an agent", () => {
    const ids = new Set(["leo"]);
    expect(assigneeAvatar("leo", ids)).toEqual({ kind: "person", initial: "L", label: "leo" });
    expect(assigneeAvatar("default", ids)).toEqual({ kind: "agent", initial: "D", label: "default · agent" });
    expect(assigneeAvatar(null, ids).kind).toBe("none");
  });

  it("prints a short age", () => {
    expect([shortAge(null), shortAge(30), shortAge(300), shortAge(7200), shortAge(3 * 86_400)]).toEqual([
      "now",
      "now",
      "5m",
      "2h",
      "3d",
    ]);
  });
});

describe("isRunStalled mirrors projects_api._run_stalled", () => {
  const running = { status: "running", started_at: NOW - 60 };

  it("is never stalled when not running or without cards", () => {
    expect(isRunStalled({ ...running, status: "waiting" }, ["todo"], 1, NOW)).toBe(false);
    expect(isRunStalled(running, [], 1, NOW)).toBe(false);
  });

  it("is not stalled while a card runs or is ready", () => {
    expect(isRunStalled(running, ["todo", "running"], 3, NOW + 9999)).toBe(false);
    expect(isRunStalled(running, ["ready"], 3, NOW + 9999)).toBe(false);
  });

  it("is stalled at once when anything in the tree is blocked", () => {
    expect(isRunStalled(running, ["todo"], 1, NOW)).toBe(true);
  });

  it("otherwise stalls only after ten minutes", () => {
    const start = { status: "running", started_at: NOW };
    expect(isRunStalled(start, ["todo"], 0, NOW + RUN_STALL_SECONDS)).toBe(false);
    expect(isRunStalled(start, ["todo"], 0, NOW + RUN_STALL_SECONDS + 1)).toBe(true);
    expect(isRunStalled({ status: "running", started_at: null }, ["todo"], 0, NOW)).toBe(false);
  });
});

describe("openRunState + laneEmpty: empty lanes tell the truth", () => {
  const context = (over: Partial<NonNullable<ProjectBoardContext["open_run"]>> = {}): ProjectBoardContext => ({
    card_runs: {},
    open_run: {
      run_no: 2,
      status: "running",
      started_at: NOW - 3600,
      card_ids: ["c1", "c2"],
      blocked_tree_count: 0,
      stalled: true,
      ...over,
    },
  });

  it("names a stalled run with a link to see why", () => {
    const run = openRunState(context(), [task("c1", "todo"), task("c2", "done")], [], NOW);
    expect(run).toEqual({ runNo: 2, status: "running", stalled: true, readyCount: 0 });
    const empty = laneEmpty("working", { filtered: false, openRun: run });
    expect(`${empty.text} — ${empty.link?.label}.`).toBe("Nothing running. Run 2 is stalled — see why.");
    expect(empty.link?.runNo).toBe(2);
  });

  it("judges the live board: an approved card un-stalls the run", () => {
    const run = openRunState(context(), [task("c1", "ready"), task("c2", "todo")], [], NOW);
    expect(run?.stalled).toBe(false);
    expect(laneEmpty("working", { filtered: false, openRun: run }).text).toBe(
      "Nothing running yet. Run 2's next card is waiting for a worker.",
    );
  });

  it("a fresh run with nothing ready is still starting", () => {
    const run = openRunState(context({ started_at: NOW - 30 }), [task("c1", "todo")], [], NOW);
    expect(laneEmpty("working", { filtered: false, openRun: run }).text).toBe(
      "Nothing running yet. Run 2 is still starting.",
    );
  });

  it("uses the server's verdict when no run card is visible", () => {
    const run = openRunState(context({ card_ids: [], stalled: true }), [], [], NOW);
    expect(run?.stalled).toBe(true);
  });

  it("a waiting run asks for a person", () => {
    const run = openRunState(context({ status: "waiting" }), [], [], NOW);
    expect(laneEmpty("working", { filtered: false, openRun: run }).text).toBe(
      "Nothing running. Run 2 is waiting for a person",
    );
  });

  it("without context, falls back to the run list and says only what it knows", () => {
    const runs = [
      { run_no: 3, status: "running", trigger: "manual", started_at: NOW, ended_at: null, duration_seconds: null, outcome: null, score_user: null },
    ] as never;
    const run = openRunState(null, [], runs, NOW);
    expect(run).toEqual({ runNo: 3, status: "running", stalled: null, readyCount: 0 });
    expect(laneEmpty("working", { filtered: false, openRun: run }).text).toBe("Nothing running. Run 3 is open");
    expect(openRunState(null, [], [], NOW)).toBeNull();
    expect(openRunState({ card_runs: {}, open_run: null }, [], runs, NOW)).toBeNull();
  });

  it("no open run, other lanes, and filtered lanes", () => {
    expect(laneEmpty("working", { filtered: false, openRun: null }).text).toBe(
      "Nothing running. No run is open.",
    );
    expect(laneEmpty("up_next", { filtered: false, openRun: null }).text).toBe("Nothing queued.");
    expect(laneEmpty("waiting", { filtered: false, openRun: null }).text).toBe(
      "Blocked or in review: nothing.",
    );
    expect(laneEmpty("done", { filtered: false, openRun: null }).text).toBe("Nothing finished yet.");
    expect(laneEmpty("working", { filtered: true, openRun: null }).text).toBe(
      "No cards match this filter.",
    );
  });
});

describe("reconcileTask / withStatus / withAssignee", () => {
  it("moves a card to the column its status names and keeps fields the answer lacks", () => {
    const b = board(task("a", "triage", { comment_count: 2 }));
    const next = reconcileTask(b, task("a", "todo"));
    expect(next.columns.find((c) => c.name === "triage")?.tasks).toEqual([]);
    const moved = next.columns.find((c) => c.name === "todo")?.tasks[0];
    expect(moved?.status).toBe("todo");
    expect(moved?.comment_count).toBe(2);
    expect(b.columns.find((c) => c.name === "triage")?.tasks).toHaveLength(1);
  });

  it("updates in place when the status is unchanged and drops archived cards", () => {
    const b = board(task("a", "ready"), task("b", "ready"));
    const renamed = reconcileTask(b, { ...task("a", "ready"), title: "New" });
    expect(renamed.columns.find((c) => c.name === "ready")?.tasks.map((t) => t.title)).toEqual([
      "New",
      "Card b",
    ]);
    expect(boardTasks(reconcileTask(b, task("a", "archived"))).map((t) => t.id)).toEqual(["b"]);
  });

  it("adds a missing column rather than losing the card", () => {
    const b: ProjectBoardView = { columns: [{ name: "triage", tasks: [task("a", "triage")] }] };
    expect(reconcileTask(b, task("a", "ready")).columns.map((c) => c.name)).toEqual(["triage", "ready"]);
  });

  it("optimistic status and assignee edits", () => {
    const b = board(task("a", "blocked"));
    expect(boardTasks(withStatus(b, "a", "ready"))[0].status).toBe("ready");
    expect(boardTasks(withAssignee(b, "a", "default"))[0].assignee).toBe("default");
    expect(withStatus(b, "nope", "ready")).toBe(b);
  });
});
