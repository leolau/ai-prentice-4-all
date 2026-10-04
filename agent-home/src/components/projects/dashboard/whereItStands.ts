import {
  agentTriageCards,
  allOutputsAccepted,
  awaitingAcceptance,
  boardTasks,
  hasInputs,
  latestRun,
  nowSeconds,
  openRun,
  outputCounts,
  runStalled,
} from "@/components/projects/dashboard/derive";
import type {
  ProjectBoardView,
  ProjectDetail,
  ProjectPlaybookResponse,
} from "@/types";

export type StepState = "done" | "attention" | "current" | "todo";

export interface StandStep {
  key: "brief" | "inputs" | "plan" | "iteration" | "review" | "done";
  label: string;
  /** Second line, e.g. "rev 4" / "working". */
  sub: string | null;
  state: StepState;
}

/**
 * Brief → Inputs → Plan (rev N) → Iteration N → Review & accept → Done, each
 * ✓ / ! / current / not yet, derived from the record. A stalled or failed
 * iteration is "attention", never "done" or a calm "current".
 */
export function standSteps(
  project: ProjectDetail,
  board: ProjectBoardView | null,
  playbook: ProjectPlaybookResponse | null,
  now: number = nowSeconds(),
): StandStep[] {
  const briefOk = Boolean(project.goal?.trim()) && project.outputs.length > 0;
  const active = playbook?.active ?? null;
  const planOk = active !== null && (active.steps ?? []).length > 0;
  const open = openRun(project);
  const latest = latestRun(project);
  const iterRun = open ?? latest;
  const accepted = allOutputsAccepted(project);
  const awaiting = awaitingAcceptance(project);
  const isDone = project.status === "done";

  let iterSub: string;
  let iterState: StepState;
  if (open) {
    if (open.status === "waiting") {
      iterSub = "waiting for you";
      iterState = "attention";
    } else if (runStalled(open, board, now)) {
      iterSub = "stalled";
      iterState = "attention";
    } else if (agentTriageCards(project, board).length > 0) {
      iterSub = "needs you";
      iterState = "attention";
    } else {
      iterSub = "working";
      iterState = "current";
    }
  } else if (latest) {
    if (latest.status === "failed" || latest.status === "blocked") {
      iterSub = latest.status;
      iterState = "attention";
    } else if (isDone || accepted) {
      iterSub = "finished";
      iterState = "done";
    } else {
      iterSub = "idle";
      iterState = awaiting > 0 ? "done" : "current";
    }
  } else {
    iterSub = "not started";
    iterState = briefOk && planOk ? "current" : "todo";
  }

  let reviewState: StepState = "todo";
  if (isDone || accepted) reviewState = "done";
  else if (!open && awaiting > 0) reviewState = "current";

  return [
    {
      key: "brief",
      label: "Brief",
      sub: null,
      state: briefOk ? "done" : "attention",
    },
    {
      key: "inputs",
      label: "Inputs",
      sub: null,
      state: hasInputs(project) ? "done" : "attention",
    },
    {
      key: "plan",
      label: "Plan",
      sub: active ? `rev ${active.rev}` : null,
      state: planOk ? "done" : "attention",
    },
    {
      key: "iteration",
      label: `Iteration ${iterRun?.run_no ?? 1}`,
      sub: iterSub,
      state: iterState,
    },
    { key: "review", label: "Review & accept", sub: null, state: reviewState },
    {
      key: "done",
      label: "Done",
      sub: null,
      state: isDone ? "done" : accepted && !open ? "current" : "todo",
    },
  ];
}

export interface BarSegment {
  key: string;
  label: string;
  count: number;
  /** 0–100, share of the bar. */
  percent: number;
}

function segments(parts: { key: string; label: string; count: number }[], total: number): BarSegment[] {
  return parts.map((part) => ({
    ...part,
    percent: total > 0 ? Math.round((100 * part.count) / total) : 0,
  }));
}

export interface OutputsBar {
  /** "0 of 2 accepted · 1 delivered · 1 in progress" */
  summary: string;
  segments: BarSegment[];
  total: number;
}

export function outputsBar(project: ProjectDetail): OutputsBar {
  const counts = outputCounts(project);
  const required = counts.required > 0 ? counts.required : counts.total;
  const bits = [`${counts.accepted} of ${required} accepted`];
  if (counts.delivered > 0) bits.push(`${counts.delivered} delivered`);
  if (counts.inProgress > 0) bits.push(`${counts.inProgress} in progress`);
  if (counts.total === 0) bits.splice(0, 1, "No outputs declared yet");
  return {
    summary: bits.join(" · "),
    total: counts.total,
    segments: segments(
      [
        { key: "accepted", label: "accepted", count: counts.accepted },
        { key: "delivered", label: "delivered", count: counts.delivered },
        { key: "in_progress", label: "in progress", count: counts.inProgress },
      ],
      counts.total,
    ),
  };
}

export interface CardsBar {
  summary: string;
  segments: BarSegment[];
  total: number;
}

/** The board's cards as done / ready-or-working / needs you. */
export function cardsBar(board: ProjectBoardView | null): CardsBar | null {
  if (!board) return null;
  const tasks = boardTasks(board);
  const done = tasks.filter((t) => t.status === "done").length;
  const needs = tasks.filter((t) => t.status === "blocked" || t.status === "triage").length;
  const working = tasks.length - done - needs;
  return {
    summary: `${done} of ${tasks.length} cards done`,
    total: tasks.length,
    segments: segments(
      [
        { key: "done", label: "done", count: done },
        { key: "working", label: "ready or working", count: working },
        { key: "needs", label: "needs you", count: needs },
      ],
      tasks.length,
    ),
  };
}
