import type {
  PlaybookStep,
  ProjectDetail,
  ProjectPlaybookResponse,
  ProjectRunBrief,
} from "@/types";

export const T0 = 1_700_000_000;

export function run(run_no: number, over: Partial<ProjectRunBrief> = {}): ProjectRunBrief {
  return {
    run_no,
    status: "done",
    trigger: "manual",
    started_at: T0 + run_no * 3600,
    ended_at: T0 + run_no * 3600 + 600,
    duration_seconds: 600,
    outcome: "delivered",
    score_user: null,
    ...over,
  };
}

export const ACTIVE_STEPS: PlaybookStep[] = [
  { key: "confirm", title: "Confirm the terms" },
  { key: "draft", title: "Draft each MOU (2.5% split)" },
  { key: "upload", title: "Upload the set" },
];

export const DRAFT_STEPS: PlaybookStep[] = [
  { key: "confirm", title: "Confirm the terms" },
  { key: "draft", title: "Re-draft each MOU (3% split)" },
  { key: "review", title: "Review checkpoint", checkpoint: true },
];

export function playbook(steps: PlaybookStep[] = ACTIVE_STEPS): ProjectPlaybookResponse {
  const active = {
    project_id: "prj_1",
    rev: 1,
    body: "",
    steps,
    active: 1,
    created_by: "leo",
    created_at: T0,
    activated_at: T0,
    note: null,
  };
  return { active, revisions: [active] };
}

export function project(over: Partial<ProjectDetail> = {}): ProjectDetail {
  return {
    id: "prj_1",
    slug: "mou-set",
    name: "MOU set",
    archived: false,
    status: "active",
    runs: [],
    outputs: [],
    members: [],
    profiles: [],
    ...over,
  } as unknown as ProjectDetail;
}
