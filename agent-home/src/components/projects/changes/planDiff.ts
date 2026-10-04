import type { PlaybookStep } from "@/types";

export type PlanDiffKind = "same" | "added" | "removed" | "changed" | "moved";

export interface PlanDiffRow {
  kind: PlanDiffKind;
  key: string;
  /** The drafted step (or, for `removed`, the active one). */
  step: PlaybookStep;
  /** The active step this row replaces (`changed` / `moved` / `same`). */
  before?: PlaybookStep;
  /** True when a `changed` step also moved. */
  moved?: boolean;
}

export interface PlanDiff {
  rows: PlanDiffRow[];
  added: number;
  removed: number;
  changed: number;
  moved: number;
  /** Nothing differs. */
  unchanged: boolean;
}

function deps(step: PlaybookStep): string {
  return [...(step.depends_on ?? step.needs ?? [])].sort().join(",");
}

function sameContent(a: PlaybookStep, b: PlaybookStep): boolean {
  return (
    a.title.trim() === b.title.trim() &&
    (a.body ?? "").trim() === (b.body ?? "").trim() &&
    Boolean(a.checkpoint) === Boolean(b.checkpoint) &&
    (a.mode ?? "card") === (b.mode ?? "card") &&
    deps(a) === deps(b)
  );
}

/** Indices (into `seq`) of one longest strictly increasing subsequence. */
function longestIncreasing(seq: number[]): Set<number> {
  const tails: number[] = [];
  const prev: number[] = new Array(seq.length).fill(-1);
  for (let i = 0; i < seq.length; i++) {
    let lo = 0;
    let hi = tails.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (seq[tails[mid]] < seq[i]) lo = mid + 1;
      else hi = mid;
    }
    if (lo > 0) prev[i] = tails[lo - 1];
    tails[lo] = i;
  }
  const keep = new Set<number>();
  let k = tails.length ? tails[tails.length - 1] : -1;
  while (k >= 0) {
    keep.add(k);
    k = prev[k];
  }
  return keep;
}

/**
 * The active plan vs the drafted one, in drafted order with removed steps
 * left where they used to be. Steps match by key, then by identical title
 * (the agent sometimes re-keys a step it kept). A matched step is `moved`
 * when it falls outside the longest run of steps that kept their order.
 */
export function planDiff(
  activeSteps: readonly PlaybookStep[],
  draftSteps: readonly PlaybookStep[],
): PlanDiff {
  const activeIndex = new Map(activeSteps.map((s, i) => [s.key, i]));
  const used = new Set<number>();
  const matchOf: (number | null)[] = draftSteps.map((step) => {
    const i = activeIndex.get(step.key);
    if (i !== undefined && !used.has(i)) {
      used.add(i);
      return i;
    }
    return null;
  });
  draftSteps.forEach((step, d) => {
    if (matchOf[d] !== null) return;
    const title = step.title.trim().toLowerCase();
    const i = activeSteps.findIndex(
      (a, ai) => !used.has(ai) && a.title.trim().toLowerCase() === title,
    );
    if (i >= 0) {
      used.add(i);
      matchOf[d] = i;
    }
  });

  const matchedDraft = matchOf
    .map((ai, d) => (ai === null ? null : d))
    .filter((d): d is number => d !== null);
  const inOrder = longestIncreasing(matchedDraft.map((d) => matchOf[d] as number));
  const stable = new Set(matchedDraft.filter((_, i) => inOrder.has(i)));

  const removedQueue = activeSteps
    .map((step, i) => ({ step, i }))
    .filter(({ i }) => !used.has(i));
  const rows: PlanDiffRow[] = [];
  const flushRemoved = (before: number) => {
    while (removedQueue.length && removedQueue[0].i < before) {
      const { step } = removedQueue.shift()!;
      rows.push({ kind: "removed", key: step.key, step });
    }
  };

  draftSteps.forEach((step, d) => {
    const ai = matchOf[d];
    if (ai === null) {
      // A replacement reads better after what it replaces: flush removed
      // steps that sit before the next step that kept its place.
      let next = Number.POSITIVE_INFINITY;
      for (let k = d + 1; k < draftSteps.length; k++) {
        if (stable.has(k)) {
          next = matchOf[k] as number;
          break;
        }
      }
      flushRemoved(next);
      rows.push({ kind: "added", key: step.key, step });
      return;
    }
    const isStable = stable.has(d);
    if (isStable) flushRemoved(ai);
    const before = activeSteps[ai];
    const same = sameContent(before, step);
    if (same && isStable) rows.push({ kind: "same", key: step.key, step, before });
    else if (same) rows.push({ kind: "moved", key: step.key, step, before });
    else rows.push({ kind: "changed", key: step.key, step, before, moved: !isStable });
  });
  flushRemoved(Number.POSITIVE_INFINITY);

  const count = (kind: PlanDiffKind) => rows.filter((r) => r.kind === kind).length;
  const added = count("added");
  const removed = count("removed");
  const changed = count("changed");
  const moved = count("moved");
  return {
    rows,
    added,
    removed,
    changed,
    moved,
    unchanged: added + removed + changed + moved === 0,
  };
}
