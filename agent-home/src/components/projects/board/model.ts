/**
 * The board, as a person reads it — pure derivations, no React.
 *
 * The kanban has eight internal stages; a person cares about four things:
 * what's next, what's being worked on, what's waiting, what's done. This
 * module groups the stages into those lanes, pulls out the cards only a
 * person can move ("Needs you"), filters by who the card belongs to, and
 * words the empty lanes so they tell the truth (a stalled run is named, not
 * hidden behind "Nothing running").
 */

import type {
  ProjectBoardContext,
  ProjectBoardTask,
  ProjectBoardView,
  ProjectDetail,
  ProjectRunBrief,
} from "@/types";

// ── Lanes ──────────────────────────────────────────────────────────────────

export type LaneKey = "up_next" | "working" | "waiting" | "done";

export interface LaneDef {
  key: LaneKey;
  label: string;
  statuses: readonly string[];
  /** The stages folded into the lane, for the lane's footer. */
  hint: string;
}

export const LANES: readonly LaneDef[] = [
  {
    key: "up_next",
    label: "Up next",
    statuses: ["triage", "todo", "scheduled", "ready"],
    hint: "Triage · To do · Scheduled · Ready",
  },
  { key: "working", label: "Working", statuses: ["running"], hint: "Running" },
  { key: "waiting", label: "Waiting", statuses: ["blocked", "review"], hint: "Blocked · In review" },
  { key: "done", label: "Done", statuses: ["done"], hint: "Done" },
];

/** The eight stages the full view shows, in the server's order. */
export const ALL_STAGES = [
  "triage",
  "todo",
  "scheduled",
  "ready",
  "running",
  "blocked",
  "review",
  "done",
] as const;

/**
 * Which lane a stage belongs to; `null` for `archived` (hidden from the
 * lanes). A stage this UI doesn't know yet reads as "Up next" — still on the
 * board, never silently dropped.
 */
export function laneForStatus(status: string): LaneKey | null {
  if (status === "archived") return null;
  for (const lane of LANES) {
    if (lane.statuses.includes(status)) return lane.key;
  }
  return "up_next";
}

/** Every visible card once, archived ones dropped. */
export function boardTasks(board: ProjectBoardView | null): ProjectBoardTask[] {
  if (!board) return [];
  const seen = new Set<string>();
  const out: ProjectBoardTask[] = [];
  for (const column of board.columns) {
    for (const task of column.tasks) {
      if (task.status === "archived" || seen.has(task.id)) continue;
      seen.add(task.id);
      out.push(task);
    }
  }
  return out;
}

const UP_NEXT_RANK: Record<string, number> = { ready: 0, scheduled: 1, todo: 2, triage: 3 };

function byOldest(a: ProjectBoardTask, b: ProjectBoardTask): number {
  return (a.created_at ?? 0) - (b.created_at ?? 0) || a.id.localeCompare(b.id);
}

/**
 * Cards per lane, each lane in the order a person reads it: Up next puts
 * the card a worker picks first on top (ready, then higher priority, then
 * oldest); Working the longest-running first; Waiting blocked before review;
 * Done the newest first.
 */
export function groupIntoLanes(tasks: ProjectBoardTask[]): Record<LaneKey, ProjectBoardTask[]> {
  const lanes: Record<LaneKey, ProjectBoardTask[]> = {
    up_next: [],
    working: [],
    waiting: [],
    done: [],
  };
  for (const task of tasks) {
    const lane = laneForStatus(task.status);
    if (lane) lanes[lane].push(task);
  }
  lanes.up_next.sort(
    (a, b) =>
      (UP_NEXT_RANK[a.status] ?? 4) - (UP_NEXT_RANK[b.status] ?? 4) ||
      (b.priority ?? 0) - (a.priority ?? 0) ||
      byOldest(a, b),
  );
  lanes.working.sort(
    (a, b) =>
      (a.started_at ?? Number.MAX_SAFE_INTEGER) - (b.started_at ?? Number.MAX_SAFE_INTEGER) ||
      byOldest(a, b),
  );
  lanes.waiting.sort(
    (a, b) =>
      (a.status === "blocked" ? 0 : 1) - (b.status === "blocked" ? 0 : 1) || byOldest(a, b),
  );
  lanes.done.sort(
    (a, b) =>
      (b.completed_at ?? b.created_at ?? 0) - (a.completed_at ?? a.created_at ?? 0) ||
      a.id.localeCompare(b.id),
  );
  return lanes;
}

/** How many Done cards show before "+N more". */
export const DONE_VISIBLE = 3;

export function splitDone(
  done: ProjectBoardTask[],
  expanded: boolean,
  visible: number = DONE_VISIBLE,
): { shown: ProjectBoardTask[]; more: number } {
  if (expanded || done.length <= visible) return { shown: done, more: 0 };
  return { shown: done.slice(0, visible), more: done.length - visible };
}

// ── Needs you ──────────────────────────────────────────────────────────────

/** What a person does to let the agent go on. */
export type NeedsKind = "unblock" | "review" | "approve";

export function needsKind(task: ProjectBoardTask): NeedsKind | null {
  if (task.status === "triage") return "approve";
  if (task.status === "blocked") return "unblock";
  if (task.status === "review") return "review";
  return null;
}

const NEEDS_RANK: Record<NeedsKind, number> = { unblock: 0, review: 1, approve: 2 };

export interface NeedsYouItem {
  task: ProjectBoardTask;
  kind: NeedsKind;
}

/**
 * The cards the agent can't move past without a person: blocked cards first
 * (work that already stopped; one waiting on an answer before any other),
 * then review, then triage — each group oldest first, since the longest
 * wait is the one to clear.
 */
export function needsYou(tasks: ProjectBoardTask[]): NeedsYouItem[] {
  const items: NeedsYouItem[] = [];
  for (const task of tasks) {
    const kind = needsKind(task);
    if (kind) items.push({ task, kind });
  }
  return items.sort(
    (a, b) =>
      NEEDS_RANK[a.kind] - NEEDS_RANK[b.kind] ||
      (a.task.block_kind === "needs_input" ? 0 : 1) -
        (b.task.block_kind === "needs_input" ? 0 : 1) ||
      byOldest(a.task, b.task),
  );
}

// ── Who: people, agents, filters ───────────────────────────────────────────

export type BoardFilter = "all" | "needs_me" | "mine" | "agent";

export interface FilterContext {
  /** The signed-in user (`callerUserId`). */
  userId: string;
  /** Whether that user may act on cards (member/lead/owner, not archived). */
  canAct: boolean;
  /** task id → the run that created it (`/board/context`). */
  cardRuns: Record<string, number>;
  /** The project's people, by user id. */
  memberIds: ReadonlySet<string>;
}

/** A viewer reads the board; members, leads and the owner act on it. */
export function canActOnBoard(
  project: Pick<ProjectDetail, "archived" | "owner_user_id" | "members">,
  userId: string,
  canLead: boolean,
): boolean {
  if (project.archived) return false;
  if (canLead || project.owner_user_id === userId) return true;
  const member = project.members.find((m) => m.user_id === userId);
  return member != null && member.role !== "viewer";
}

/** Who created the card, with the kanban's `user:` actor prefix dropped. */
export function createdBy(task: ProjectBoardTask): string | null {
  const raw = task.created_by;
  if (typeof raw !== "string" || !raw) return null;
  return raw.startsWith("user:") ? raw.slice(5) : raw;
}

export function cardRunNo(task: ProjectBoardTask, cardRuns: Record<string, number>): number | null {
  const n = cardRuns[task.id];
  return typeof n === "number" ? n : null;
}

/** Assigned to me, or a card I made by hand (a run's cards are the agent's). */
export function isMine(task: ProjectBoardTask, ctx: FilterContext): boolean {
  if (task.assignee === ctx.userId) return true;
  return createdBy(task) === ctx.userId && cardRunNo(task, ctx.cardRuns) == null;
}

/** Made by a run, or in an agent profile's hands. */
export function isAgentCard(task: ProjectBoardTask, ctx: FilterContext): boolean {
  if (cardRunNo(task, ctx.cardRuns) != null) return true;
  if (task.assignee && !ctx.memberIds.has(task.assignee) && task.assignee !== ctx.userId) {
    return true;
  }
  const by = createdBy(task);
  return by != null && by !== ctx.userId && !ctx.memberIds.has(by);
}

/** A card waiting on a person — and this person is allowed to act. */
export function needsMe(task: ProjectBoardTask, ctx: FilterContext): boolean {
  return ctx.canAct && needsKind(task) != null;
}

export function matchesFilter(
  task: ProjectBoardTask,
  filter: BoardFilter,
  ctx: FilterContext,
): boolean {
  switch (filter) {
    case "needs_me":
      return needsMe(task, ctx);
    case "mine":
      return isMine(task, ctx);
    case "agent":
      return isAgentCard(task, ctx);
    default:
      return true;
  }
}

export function filterTasks(
  tasks: ProjectBoardTask[],
  filter: BoardFilter,
  ctx: FilterContext,
  runNo: number | null = null,
): ProjectBoardTask[] {
  return tasks.filter(
    (task) =>
      matchesFilter(task, filter, ctx) &&
      (runNo == null || cardRunNo(task, ctx.cardRuns) === runNo),
  );
}

export function filterCounts(
  tasks: ProjectBoardTask[],
  ctx: FilterContext,
): Record<BoardFilter, number> {
  return {
    all: tasks.length,
    needs_me: tasks.filter((t) => needsMe(t, ctx)).length,
    mine: tasks.filter((t) => isMine(t, ctx)).length,
    agent: tasks.filter((t) => isAgentCard(t, ctx)).length,
  };
}

/** The runs the visible cards came from, newest first. */
export function runNumbers(tasks: ProjectBoardTask[], cardRuns: Record<string, number>): number[] {
  const nums = new Set<number>();
  for (const task of tasks) {
    const n = cardRunNo(task, cardRuns);
    if (n != null) nums.add(n);
  }
  return [...nums].sort((a, b) => b - a);
}

export interface Avatar {
  kind: "person" | "agent" | "none";
  initial: string;
  label: string;
}

/** A person's initial, an agent's mark, or "no one yet". */
export function assigneeAvatar(
  assignee: string | null,
  memberIds: ReadonlySet<string>,
): Avatar {
  if (!assignee) return { kind: "none", initial: "?", label: "no one yet" };
  const initial = assignee.trim().charAt(0).toUpperCase() || "?";
  if (memberIds.has(assignee)) return { kind: "person", initial, label: assignee };
  return { kind: "agent", initial, label: `${assignee} · agent` };
}

// ── Status and time ────────────────────────────────────────────────────────

export const STATUS_LABEL: Record<string, string> = {
  triage: "triage",
  todo: "to do",
  scheduled: "scheduled",
  ready: "ready",
  running: "running",
  blocked: "blocked",
  review: "in review",
  done: "done",
  archived: "archived",
};

export type StatusTone = "warn" | "accent" | "ok" | "muted";

export function statusTone(status: string): StatusTone {
  if (status === "triage" || status === "blocked" || status === "review") return "warn";
  if (status === "ready" || status === "running") return "accent";
  if (status === "done") return "ok";
  return "muted";
}

/** "now" | "5m" | "3h" | "2d" — a tile's age. */
export function shortAge(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 60) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

export function taskAgeSeconds(task: ProjectBoardTask, now: number): number | null {
  const age = task.age as { created_age_seconds?: number | null } | null | undefined;
  if (age && typeof age.created_age_seconds === "number") return age.created_age_seconds;
  return task.created_at ? Math.max(0, now - task.created_at) : null;
}

export function commentCount(task: ProjectBoardTask): number {
  return typeof task.comment_count === "number" ? task.comment_count : 0;
}

// ── The open run: stalled or not ───────────────────────────────────────────

/** `projects_api._RUN_STALL_SECONDS`. */
export const RUN_STALL_SECONDS = 10 * 60;

/**
 * `projects_api._run_stalled`, verbatim: a `running` run with cards none
 * of which is running or ready is stalled at once when anything in its
 * dependency tree is blocked, and otherwise once it has been open for over
 * ten minutes (the dispatcher promotes ready cards within a minute).
 */
export function isRunStalled(
  run: { status: string; started_at: number | null },
  cardStatuses: (string | null)[],
  blockedTreeCount: number,
  now: number,
): boolean {
  if (run.status !== "running" || cardStatuses.length === 0) return false;
  if (cardStatuses.some((s) => s === "running" || s === "ready")) return false;
  if (blockedTreeCount > 0) return true;
  return run.started_at != null && Math.floor(now) - Math.floor(run.started_at) > RUN_STALL_SECONDS;
}

export interface OpenRunState {
  runNo: number;
  status: string;
  /** `null` when the board context could not be read. */
  stalled: boolean | null;
  /** The run's cards a worker can pick up right now. */
  readyCount: number;
}

/**
 * The open run, judged against the *live* board (so an optimistic approve
 * un-stalls it at once). Without the board context only the run number is
 * known, from the project's run list.
 */
export function openRunState(
  context: ProjectBoardContext | null,
  tasks: ProjectBoardTask[],
  runs: ProjectRunBrief[] | undefined,
  now: number,
): OpenRunState | null {
  const open = context?.open_run;
  if (open) {
    const byId = new Map(tasks.map((t) => [t.id, t.status]));
    const statuses = open.card_ids.map((id) => byId.get(id) ?? "archived");
    const stalled =
      statuses.length > 0
        ? isRunStalled(open, statuses, open.blocked_tree_count, now)
        : open.stalled;
    return {
      runNo: open.run_no,
      status: open.status,
      stalled,
      readyCount: statuses.filter((s) => s === "ready").length,
    };
  }
  if (context) return null;
  const brief = (runs ?? []).find((r) => r.status === "running" || r.status === "waiting");
  if (!brief) return null;
  return { runNo: brief.run_no, status: brief.status, stalled: null, readyCount: 0 };
}

export interface LaneEmpty {
  text: string;
  /** A trailing link to the run page ("see why"). */
  link?: { label: string; runNo: number };
}

/** The sentence an empty lane shows — what is true, not just "empty". */
export function laneEmpty(
  lane: LaneKey,
  opts: { filtered: boolean; openRun: OpenRunState | null },
): LaneEmpty {
  if (opts.filtered) return { text: "No cards match this filter." };
  switch (lane) {
    case "up_next":
      return { text: "Nothing queued." };
    case "waiting":
      return { text: "Blocked or in review: nothing." };
    case "done":
      return { text: "Nothing finished yet." };
    case "working": {
      const run = opts.openRun;
      if (!run) return { text: "Nothing running. No run is open." };
      const name = `Run ${run.runNo}`;
      if (run.status === "waiting") {
        return { text: `Nothing running. ${name} is waiting for a person`, link: { label: "open it", runNo: run.runNo } };
      }
      if (run.stalled === true) {
        return { text: `Nothing running. ${name} is stalled`, link: { label: "see why", runNo: run.runNo } };
      }
      if (run.stalled === null) {
        return { text: `Nothing running. ${name} is open`, link: { label: "open it", runNo: run.runNo } };
      }
      if (run.readyCount > 0) {
        return { text: `Nothing running yet. ${name}'s next card is waiting for a worker.` };
      }
      return { text: `Nothing running yet. ${name} is still starting.` };
    }
  }
}

// ── Local board edits (optimistic + reconcile) ─────────────────────────────

export function findTask(board: ProjectBoardView | null, taskId: string): ProjectBoardTask | null {
  if (!board) return null;
  for (const column of board.columns) {
    const task = column.tasks.find((t) => t.id === taskId);
    if (task) return task;
  }
  return null;
}

/**
 * Put a card where its `status` says, with the given fields — the server's
 * answer after a write (which may differ from the optimistic guess: a
 * parent-gated approve lands in `todo`) or the snapshot a failed write rolls
 * back to. Fields the answer doesn't carry (comment counts) are kept; an
 * `archived` card leaves the board.
 */
export function reconcileTask(
  board: ProjectBoardView,
  task: ProjectBoardTask,
): ProjectBoardView {
  let previous: ProjectBoardTask | null = null;
  let placed = false;
  const columns = board.columns.map((column) => {
    const idx = column.tasks.findIndex((t) => t.id === task.id);
    if (idx < 0) return column;
    previous = column.tasks[idx];
    if (column.name === task.status) {
      placed = true;
      const tasks = [...column.tasks];
      tasks[idx] = { ...column.tasks[idx], ...task };
      return { ...column, tasks };
    }
    return { ...column, tasks: column.tasks.filter((t) => t.id !== task.id) };
  });
  if (placed || task.status === "archived") return { ...board, columns };
  const merged: ProjectBoardTask = previous
    ? { ...(previous as ProjectBoardTask), ...task }
    : task;
  if (!columns.some((c) => c.name === task.status)) {
    return { ...board, columns: [...columns, { name: task.status, tasks: [merged] }] };
  }
  return {
    ...board,
    columns: columns.map((c) =>
      c.name === task.status ? { ...c, tasks: [...c.tasks, merged] } : c,
    ),
  };
}

/** Optimistic move: the card, with only its status changed. */
export function withStatus(board: ProjectBoardView, taskId: string, status: string): ProjectBoardView {
  const task = findTask(board, taskId);
  return task ? reconcileTask(board, { ...task, status }) : board;
}

/** Optimistic assign. */
export function withAssignee(
  board: ProjectBoardView,
  taskId: string,
  assignee: string | null,
): ProjectBoardView {
  const task = findTask(board, taskId);
  return task ? reconcileTask(board, { ...task, assignee }) : board;
}

/** A server answer is a card row when it carries an id and a status. */
export function isTaskRow(data: unknown): data is ProjectBoardTask {
  return (
    !!data &&
    typeof data === "object" &&
    typeof (data as { id?: unknown }).id === "string" &&
    typeof (data as { status?: unknown }).status === "string"
  );
}
