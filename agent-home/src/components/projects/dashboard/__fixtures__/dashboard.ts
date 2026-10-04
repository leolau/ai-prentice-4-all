import type {
  ProjectBoardTask,
  ProjectBoardView,
  ProjectDetail,
  ProjectDirective,
  ProjectLink,
  ProjectOutputWithDeliveries,
  ProjectPlaybookResponse,
  ProjectRunBrief,
} from "@/types";

/** Shared test fixtures for the Dashboard: a fixed clock and builders. */
export const NOW = 1_760_000_000;

export function project(over: Partial<ProjectDetail> = {}): ProjectDetail {
  return {
    id: "prj_1",
    slug: "mou-set",
    name: "Four MOUs",
    description: "Draft the MOUs.",
    icon: null,
    color: null,
    board_slug: "mou",
    primary_path: null,
    archived: false,
    created_at: NOW - 10 * 86_400,
    goal: "Four signed-ready MOUs, one per partner",
    visibility: "shared",
    owner_user_id: "yan",
    status: "active",
    cadence: "one_off",
    schedule: null,
    review_every: null,
    autonomy: "supervised",
    max_in_progress: 2,
    budget_usd_per_run: null,
    definition_of_done: "All four accepted by Yan",
    target_audience: null,
    score_rubric: null,
    toolsets: "file",
    skills: null,
    due_at: null,
    host_profile: "default",
    cron_job_id: null,
    summary: null,
    summary_at: null,
    last_reviewed_at: null,
    next_run_at: null,
    outputs: [output()],
    members: [{ project_id: "prj_1", user_id: "yan", role: "lead", added_by: null, added_at: NOW }],
    profiles: [
      { project_id: "prj_1", profile: "default", role: "host", added_by: null, added_at: NOW },
    ],
    contacts: [],
    links: { file: [link({ kind: "file", ref: "template.docx" })] },
    progress: {
      rung: "outputs",
      label: "outputs",
      headline: "0 of 1 outputs accepted",
      accepted: 0,
      required: 1,
      cards: { total: 0, done: 0, running: 0, blocked: 0 },
    },
    score: null,
    health: "ok",
    runs: [],
    card_rollup: { total: 0, done: 0, running: 0, blocked: 0 },
    recent_events: [],
    ...over,
  } as ProjectDetail;
}

export function output(over: Partial<ProjectOutputWithDeliveries> = {}): ProjectOutputWithDeliveries {
  return {
    id: "out_1",
    project_id: "prj_1",
    seq: 1,
    title: "4 MOUs in docx",
    spec: null,
    kind: "file",
    required: 1,
    recurring: 0,
    status: "pending",
    delivered_at: null,
    accepted_at: null,
    accepted_by: null,
    created_at: NOW,
    deliveries: [],
    ...over,
  };
}

export function link(over: Partial<ProjectLink> = {}): ProjectLink {
  return {
    project_id: "prj_1",
    kind: "file",
    profile: "default",
    ref: "x",
    label: null,
    added_by: "yan",
    added_at: NOW,
    resolved: true,
    ...over,
  };
}

export function run(over: Partial<ProjectRunBrief> = {}): ProjectRunBrief {
  return {
    run_no: 1,
    status: "done",
    trigger: "manual",
    started_at: NOW - 3_600,
    ended_at: NOW - 1_800,
    duration_seconds: 1_800,
    outcome: null,
    score_user: null,
    ...over,
  };
}

export function card(over: Partial<ProjectBoardTask> = {}): ProjectBoardTask {
  return {
    id: "task_1",
    title: "Draft MOU",
    body: null,
    status: "ready",
    assignee: null,
    priority: 0,
    created_at: NOW - 3_600,
    started_at: null,
    completed_at: null,
    tenant: null,
    project_id: "prj_1",
    result: null,
    current_step_key: null,
    ...over,
  };
}

export function board(...tasks: ProjectBoardTask[]): ProjectBoardView {
  const names = Array.from(new Set(tasks.map((t) => t.status)));
  return {
    columns: names.map((name) => ({ name, tasks: tasks.filter((t) => t.status === name) })),
  };
}

export function playbook(
  steps: { key: string; title: string; checkpoint?: boolean }[] = [{ key: "a", title: "Draft" }],
  rev = 3,
): ProjectPlaybookResponse {
  return {
    active: {
      project_id: "prj_1",
      rev,
      body: "",
      steps,
      active: 1,
      created_by: "yan",
      created_at: NOW,
      activated_at: NOW,
      note: null,
    },
    revisions: [],
  } as ProjectPlaybookResponse;
}

export function directive(over: Partial<ProjectDirective> = {}): ProjectDirective {
  return {
    id: "dir_1",
    project_id: "prj_1",
    kind: "directive",
    body: "Use Hong Kong governing law.",
    scope: "project",
    target_ref: null,
    rating: null,
    author_user_id: "yan",
    created_at: NOW - 86_400,
    active: 1,
    retired_at: null,
    superseded_by: null,
    ...over,
  };
}
