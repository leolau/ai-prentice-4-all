import type { ReadinessItem } from "@/components/projects/readiness";
import type {
  ClarifyStatus,
  ClarifySummary,
  ProjectBoardTask,
  ProjectBoardView,
  ProjectDetail,
  ProjectLinkKind,
  ProjectPlaybookResponse,
  ProjectRunBrief,
} from "@/types";

/**
 * Small, pure reads over the project detail + board that every Dashboard
 * card shares, so the hero, the "Needs you" list and the stepper can never
 * disagree about what is open, stalled or waiting.
 */

export function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

/** Every card on the board, archived ones dropped. */
export function boardTasks(board: ProjectBoardView | null): ProjectBoardTask[] {
  if (!board) return [];
  return board.columns
    .flatMap((column) => column.tasks)
    .filter((task) => task.status !== "archived");
}

/** Runs newest first. */
export function runsNewestFirst(project: ProjectDetail): ProjectRunBrief[] {
  return [...project.runs].sort((a, b) => b.run_no - a.run_no);
}

export function latestRun(project: ProjectDetail): ProjectRunBrief | null {
  return runsNewestFirst(project)[0] ?? null;
}

/** The run still holding the project's one open slot (running or waiting). */
export function openRun(project: ProjectDetail): ProjectRunBrief | null {
  return (
    runsNewestFirst(project).find(
      (run) => run.status === "running" || run.status === "waiting",
    ) ?? null
  );
}

/** Mirrors `_RUN_STALL_SECONDS` in `hermes_cli/projects_api.py`. */
export const RUN_STALL_SECONDS = 10 * 60;

/**
 * Whether a `running` run has no worker behind it — the same rule as the
 * server's `_run_stalled`, applied to the project board because the detail
 * read's run brief does not carry the per-run cards. A server-sent
 * `stalled` flag wins when present. With no board we cannot tell, so we
 * do not claim a stall.
 */
export function runStalled(
  run: ProjectRunBrief,
  board: ProjectBoardView | null,
  now: number = nowSeconds(),
): boolean {
  if (typeof run.stalled === "boolean") return run.status === "running" && run.stalled;
  if (run.status !== "running" || !board) return false;
  const tasks = boardTasks(board);
  if (tasks.length === 0) return false;
  if (tasks.some((task) => task.status === "running" || task.status === "ready")) {
    return false;
  }
  if (tasks.some((task) => task.status === "blocked")) return true;
  return now - run.started_at > RUN_STALL_SECONDS;
}

/** Who on this box is a person (as opposed to a profile/worker/system). */
export function humanIds(project: ProjectDetail): Set<string> {
  const ids = new Set(project.members.map((member) => member.user_id));
  if (project.owner_user_id) ids.add(project.owner_user_id);
  return ids;
}

/**
 * Cards sitting in Triage that the agent made (a worker's `kanban_create`
 * or a decomposition) — the ones a person must approve before any worker
 * can pick them up. Cards a person filed, and a run's own checkpoint-held
 * successors (created by the run's trigger — a person, or `"projects"` for
 * a scheduled run), are not ours to approve here: Continue releases those.
 */
export function agentTriageCards(
  project: ProjectDetail,
  board: ProjectBoardView | null,
): ProjectBoardTask[] {
  const people = humanIds(project);
  return boardTasks(board).filter((task) => {
    if (task.status !== "triage") return false;
    const by = typeof task.created_by === "string" ? task.created_by : null;
    if (by === "projects") return false;
    return by === null || !people.has(by);
  });
}

export function blockedCards(board: ProjectBoardView | null): ProjectBoardTask[] {
  return boardTasks(board)
    .filter((task) => task.status === "blocked")
    .sort((a, b) => b.priority - a.priority || a.created_at - b.created_at);
}

const INPUT_KINDS: ProjectLinkKind[] = ["file", "memory", "reference", "sample"];

/** Any file / memory / reference / sample attached for the agent to read. */
export function hasInputs(project: ProjectDetail): boolean {
  return INPUT_KINDS.some((kind) => (project.links[kind] ?? []).length > 0);
}

export interface OutputCounts {
  /** Non-dropped outputs. */
  total: number;
  required: number;
  accepted: number;
  /** Delivered but not accepted. */
  delivered: number;
  inProgress: number;
  pending: number;
}

export function outputCounts(project: ProjectDetail): OutputCounts {
  const live = project.outputs.filter((output) => output.status !== "dropped");
  const count = (status: string) => live.filter((o) => o.status === status).length;
  return {
    total: live.length,
    required: live.filter((output) => Boolean(output.required)).length,
    accepted: count("accepted"),
    delivered: count("delivered"),
    inProgress: count("in_progress"),
    pending: count("pending"),
  };
}

/** Delivered outputs still waiting on a person's acceptance. */
export function awaitingAcceptance(project: ProjectDetail): number {
  return project.output_rollup?.awaiting_acceptance ?? outputCounts(project).delivered;
}

/** Every required output (or, with none marked required, every output) accepted. */
export function allOutputsAccepted(project: ProjectDetail): boolean {
  const live = project.outputs.filter((output) => output.status !== "dropped");
  if (live.length === 0) return false;
  const required = live.filter((output) => Boolean(output.required));
  const set = required.length > 0 ? required : live;
  return set.every((output) => output.status === "accepted");
}

export function firstUnmet(readiness: ReadinessItem[]): ReadinessItem | null {
  return readiness.find((item) => !item.ok) ?? null;
}

/** "“A”, “B” and “C”" — up to three titles, then "and N more". */
export function quoteTitles(titles: string[], max = 3): string {
  const quoted = titles.slice(0, max).map((title) => `“${title}”`);
  const rest = titles.length - quoted.length;
  if (rest > 0) return `${quoted.join(", ")} and ${rest} more`;
  if (quoted.length <= 1) return quoted.join("");
  return `${quoted.slice(0, -1).join(", ")} and ${quoted[quoted.length - 1]}`;
}

export function plural(n: number, one: string, many: string = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "5 min" / "2 h" / "3 d" — how long something has been quiet. */
export function spanLabel(seconds: number): string {
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86_400)} d`;
}

/** An active plan revision with at least one step. */
export function hasActivePlan(playbook: ProjectPlaybookResponse | null): boolean {
  return (playbook?.active?.steps ?? []).length > 0;
}

/**
 * The project's scope clarification summary. Older servers send no
 * `clarify` at all; that reads as "not started".
 */
export function clarifyOf(project: ProjectDetail): ClarifySummary {
  return (
    project.clarify ?? {
      status: "not_started",
      round: 0,
      open_count: 0,
      answered_count: 0,
      understanding: null,
      confirmed_at: null,
    }
  );
}

export function clarifyStatus(project: ProjectDetail): ClarifyStatus {
  return clarifyOf(project).status;
}

/**
 * Scope was never discussed, but the project is already past it — it has
 * an active plan or has run — so asking about scope now would only nag.
 */
export function scopeSkipped(
  project: ProjectDetail,
  playbook: ProjectPlaybookResponse | null,
): boolean {
  return (
    clarifyStatus(project) === "not_started" &&
    (hasActivePlan(playbook) || project.runs.length > 0)
  );
}

/**
 * Whether the Dashboard should suggest letting the agent ask about scope
 * before a plan exists: never discussed, no active plan, no plan revision
 * waiting, no runs. An unloaded plan (`null`) is unknown, so we stay quiet.
 */
export function suggestScopeFirst(
  project: ProjectDetail,
  playbook: ProjectPlaybookResponse | null,
): boolean {
  if (project.archived || project.status === "done") return false;
  if (playbook === null) return false;
  if (clarifyStatus(project) !== "not_started") return false;
  if (scopeSkipped(project, playbook)) return false;
  return (playbook.revisions ?? []).length === 0;
}
