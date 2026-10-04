import type {
  ProjectBoardContext,
  ProjectBoardTask,
  ProjectBoardView,
  ProjectDetail,
} from "@/types";

export const NOW = 1_800_000_000;

export function card(
  id: string,
  status: string,
  over: Partial<ProjectBoardTask> = {},
): ProjectBoardTask {
  return {
    id,
    title: `Card ${id}`,
    body: null,
    status,
    assignee: null,
    priority: 0,
    created_at: NOW - 2 * 86_400,
    started_at: null,
    completed_at: null,
    tenant: null,
    project_id: "prj_1",
    result: null,
    current_step_key: null,
    ...over,
  };
}

const STAGES = ["triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done"];

export function boardOf(tasks: ProjectBoardTask[]): ProjectBoardView {
  return {
    now: NOW,
    columns: STAGES.map((name) => ({ name, tasks: tasks.filter((t) => t.status === name) })),
  };
}

export const TASKS: ProjectBoardTask[] = [
  card("tri_a", "triage", { title: "Produce four Chinese MOUs", created_at: NOW - 3000, created_by: "leo" }),
  card("tri_b", "triage", { title: "Re-verify all clauses", created_at: NOW - 2000, created_by: "leo" }),
  card("tri_c", "triage", { title: "Upload version 2 set", created_at: NOW - 1000, created_by: "yan" }),
  card("blk", "blocked", { title: "Send the digest", block_kind: "needs_input", created_by: "leo" }),
  card("rev", "review", { title: "Check the tone", created_by: "leo" }),
  card("rdy", "ready", { title: "Revise MOUs to five-party structure", assignee: "default", comment_count: 2, created_by: "leo" }),
  ...Array.from({ length: 5 }, (_, i) =>
    card(`done_${i}`, "done", { title: `Finished ${i}`, completed_at: NOW - i * 100, created_by: "leo" }),
  ),
];

export const BOARD = boardOf(TASKS);

export const CONTEXT: ProjectBoardContext = {
  card_runs: { tri_a: 2, tri_b: 2, blk: 2, rdy: 1, done_0: 1 },
  open_run: {
    run_no: 2,
    status: "running",
    started_at: NOW - 3600,
    card_ids: ["tri_a", "tri_b", "blk"],
    blocked_tree_count: 0,
    stalled: true,
  },
};

export const PROJECT = {
  id: "prj_1",
  slug: "mous",
  name: "Five-party MOUs",
  archived: false,
  owner_user_id: "leo",
  host_profile: "default",
  members: [
    { project_id: "prj_1", user_id: "leo", role: "lead", added_by: null, added_at: 0 },
    { project_id: "prj_1", user_id: "yan", role: "member", added_by: null, added_at: 0 },
    { project_id: "prj_1", user_id: "vic", role: "viewer", added_by: null, added_at: 0 },
  ],
  profiles: [{ project_id: "prj_1", profile: "researcher", role: "worker", added_by: null, added_at: 0 }],
  runs: [],
} as unknown as ProjectDetail;
