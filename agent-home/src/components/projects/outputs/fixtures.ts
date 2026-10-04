/** Shared test fixtures for the Outputs tab and shelf tests. */
import type { ProjectTabProps } from "@/components/projects/tabs/types";
import type {
  ProjectArtifact,
  ProjectDelivery,
  ProjectDetail,
  ProjectOutputWithDeliveries,
} from "@/types";

export const NOW = 1_760_000_000;

export const DELIVERY = (over: Partial<ProjectDelivery> = {}): ProjectDelivery => ({
  id: "del_1",
  output_id: "out_1",
  run_id: "run_a",
  task_id: null,
  link_kind: "url",
  link_ref: "https://example.com/mou-v1.docx",
  profile: "default",
  label: null,
  note: null,
  delivered_at: NOW - 3_600,
  ...over,
});

export const OUTPUT = (
  over: Partial<ProjectOutputWithDeliveries> = {},
): ProjectOutputWithDeliveries => ({
  id: "out_1",
  project_id: "prj_1",
  seq: 1,
  title: "2 MOUs in docx",
  spec: null,
  kind: "file",
  required: 1,
  recurring: 0,
  status: "pending",
  delivered_at: null,
  accepted_at: null,
  accepted_by: null,
  created_at: NOW - 86_400,
  deliveries: [],
  ...over,
});

export const ARTIFACT = (over: Partial<ProjectArtifact> = {}): ProjectArtifact => ({
  id: "f:t_1:abc",
  title: "mou-qingtian.docx",
  kind: "draft",
  ext: "docx",
  mime: null,
  href: "/api/projects/mou/artifacts/f%3At_1%3Aabc/content",
  location: "card workspace",
  source: "card_file",
  link_kind: "workspace",
  link_ref: "/ws/t_1/mou-qingtian.docx",
  run_id: "run_a",
  run_no: 1,
  card_id: "t_1",
  card_title: "Draft both MOUs",
  output_id: null,
  output_title: null,
  version: null,
  created_at: NOW - 1_000,
  created_by: "default",
  ...over,
});

export const PROJECT = (over: Partial<ProjectDetail> = {}): ProjectDetail =>
  ({
    id: "prj_1",
    slug: "mou",
    name: "Sign the MOUs",
    description: "Two bilingual MOUs.",
    icon: null,
    color: null,
    board_slug: null,
    primary_path: null,
    archived: false,
    created_at: NOW - 30 * 86_400,
    goal: "Both MOUs signed",
    visibility: "shared",
    owner_user_id: "leo",
    status: "active",
    cadence: "one_off",
    schedule: null,
    review_every: null,
    autonomy: "supervised",
    max_in_progress: 1,
    budget_usd_per_run: null,
    definition_of_done: null,
    target_audience: null,
    score_rubric: null,
    toolsets: null,
    skills: null,
    due_at: null,
    host_profile: "default",
    cron_job_id: null,
    summary: null,
    summary_at: null,
    last_reviewed_at: null,
    next_run_at: null,
    outputs: [],
    members: [],
    profiles: [],
    contacts: [],
    links: {},
    progress: null,
    score: null,
    health: "ok",
    runs: [],
    card_rollup: { total: 0, done: 0, running: 0, blocked: 0 },
    recent_events: [],
    ...over,
  }) as unknown as ProjectDetail;

export const TAB_PROPS = (
  project: ProjectDetail,
  over: Partial<ProjectTabProps> = {},
): ProjectTabProps => ({
  project,
  board: null,
  playbook: null,
  directives: null,
  doctor: null,
  callerUserId: "leo",
  canLead: true,
  readiness: [],
  runnable: false,
  onNavigate: () => {},
  onChangeRequest: () => {},
  ...over,
});

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}
