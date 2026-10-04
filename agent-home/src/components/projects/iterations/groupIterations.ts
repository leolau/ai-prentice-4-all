import type {
  PlaybookRev,
  ProjectChange,
  ProjectChangeKind,
  ProjectChangesHistory,
  ProjectDelivery,
  ProjectDetail,
  ProjectDirective,
  ProjectDirectivesResponse,
  ProjectPlaybookResponse,
  ProjectRunBrief,
  ProjectRunStatus,
} from "@/types";

export type IterationStatus = ProjectRunStatus | "upcoming";

export interface IterationDelivery {
  outputTitle: string;
  delivery: ProjectDelivery;
}

export interface IterationRun {
  run_no: number;
  status: ProjectRunStatus;
  started_at: number;
  ended_at: number | null;
  duration_seconds: number | null;
  outcome: string | null;
  score_user: number | null;
  planRev: number | null;
  deliveries: IterationDelivery[];
  cardsDone: number | null;
  cardsTotal: number | null;
}

export interface Requirement {
  id: string;
  body: string;
  author: string;
  createdAt: number;
  kinds: ProjectChangeKind[];
  status: "active" | "retired" | "proposed";
  retiredAt: number | null;
  supersededBy: string | null;
  /** The first iteration whose runs carried it; null when none has yet. */
  firstIteration: number | null;
}

export interface Iteration {
  no: number;
  status: IterationStatus;
  planRev: number | null;
  /** Requirement ids in force for this iteration. */
  requirementIds: string[];
  /** Requirements new since the previous iteration. */
  added: Requirement[];
  /** Requirements dropped since the previous iteration. */
  retired: Requirement[];
  runs: IterationRun[];
  deliveries: IterationDelivery[];
  startedAt: number | null;
  endedAt: number | null;
  /** The iteration to show expanded: the open one, else the newest started. */
  current: boolean;
}

export interface IterationHistory {
  /** Newest first; an `upcoming` iteration leads when the next run would differ. */
  iterations: Iteration[];
  /** Newest first. */
  requirements: Requirement[];
  /** The open run (running/waiting), if any. */
  openRun: IterationRun | null;
  /** Iterations that have runs. */
  startedCount: number;
  /** The number the next started iteration gets. */
  nextIterationNo: number;
}

type DirectiveLike = ProjectDirective & Partial<Pick<ProjectChange, "kinds">>;
type RunLike = ProjectRunBrief & {
  id?: string;
  playbook_rev?: number | null;
  cards_done?: number;
  cards_total?: number;
};

const OPEN: readonly ProjectRunStatus[] = ["running", "waiting"];

function inForceAt(d: DirectiveLike, t: number): boolean {
  if (d.created_at > t) return false;
  if (d.retired_at != null) return d.retired_at > t;
  return d.active === 1;
}

function revAt(revisions: readonly PlaybookRev[], t: number): number | null {
  let best: PlaybookRev | null = null;
  for (const rev of revisions) {
    if (rev.activated_at == null || rev.activated_at > t) continue;
    if (!best || rev.activated_at > (best.activated_at ?? 0)) best = rev;
  }
  return best ? best.rev : null;
}

function signature(rev: number | null, ids: readonly string[]): string {
  return `${rev ?? "-"}|${[...ids].sort().join(",")}`;
}

/**
 * The project's history as iterations: each is one requirement version
 * (the directives in force) plus the plan revision it ran on, with the
 * consecutive runs that shared both and what they delivered. When the
 * current requirements or plan differ from the last iteration's (or
 * nothing has run yet) an `upcoming` iteration leads the list.
 *
 * `history` (`GET /changes`) carries retired directives, change kinds and
 * each run's pinned plan revision; without it the page's own data is used
 * and the revision is inferred from activation times.
 */
export function groupIterations(
  project: Pick<ProjectDetail, "runs" | "outputs">,
  directives: ProjectDirectivesResponse | null,
  playbook: ProjectPlaybookResponse | null,
  history?: ProjectChangesHistory | null,
): IterationHistory {
  const all: DirectiveLike[] = history
    ? history.changes
    : [...(directives?.directives ?? []), ...(directives?.proposed ?? [])];
  const reqs = all.filter((d) => d.kind === "directive");
  const revisions = playbook?.revisions ?? [];
  const runs: RunLike[] = [...(history?.runs ?? project.runs ?? [])].sort(
    (a, b) => a.run_no - b.run_no,
  );

  const deliveries: (IterationDelivery & { claimed?: boolean })[] = (
    project.outputs ?? []
  ).flatMap((out) =>
    (out.deliveries ?? []).map((delivery) => ({ outputTitle: out.title, delivery })),
  );
  const runDeliveries = (run: RunLike): IterationDelivery[] => {
    const mine = deliveries.filter((d) => {
      if (d.claimed) return false;
      if (run.id && d.delivery.run_id) return d.delivery.run_id === run.id;
      // No run ids on the page: fall back to the run's time window.
      const end = run.ended_at ?? Number.POSITIVE_INFINITY;
      return d.delivery.delivered_at >= run.started_at && d.delivery.delivered_at <= end;
    });
    mine.forEach((d) => (d.claimed = true));
    return mine
      .map(({ outputTitle, delivery }) => ({ outputTitle, delivery }))
      .sort((a, b) => b.delivery.delivered_at - a.delivery.delivered_at);
  };

  // Newest runs claim deliveries first so an overlap lands on the later run.
  const runRows = new Map<number, IterationRun>();
  for (const run of [...runs].reverse()) {
    runRows.set(run.run_no, {
      run_no: run.run_no,
      status: run.status,
      started_at: run.started_at,
      ended_at: run.ended_at,
      duration_seconds: run.duration_seconds,
      outcome: run.outcome,
      score_user: run.score_user,
      planRev: run.playbook_rev ?? revAt(revisions, run.started_at),
      deliveries: runDeliveries(run),
      cardsDone: run.cards_done ?? null,
      cardsTotal: run.cards_total ?? null,
    });
  }

  const byId = new Map(reqs.map((d) => [d.id, d]));
  const toRequirement = (d: DirectiveLike): Requirement => ({
    id: d.id,
    body: d.body,
    author: d.author_user_id,
    createdAt: d.created_at,
    kinds: (d.kinds ?? []) as ProjectChangeKind[],
    status: d.active === 1 ? "active" : d.retired_at != null ? "retired" : "proposed",
    retiredAt: d.retired_at,
    supersededBy: d.superseded_by,
    firstIteration: null,
  });
  const requirements = new Map(reqs.map((d) => [d.id, toRequirement(d)]));

  const started: Iteration[] = [];
  let prevIds: string[] = [];
  let prevSig: string | null = null;
  for (const run of runs) {
    const row = runRows.get(run.run_no)!;
    const ids = reqs.filter((d) => inForceAt(d, run.started_at)).map((d) => d.id);
    const sig = signature(row.planRev, ids);
    if (sig !== prevSig) {
      const no = started.length + 1;
      started.push({
        no,
        status: row.status,
        planRev: row.planRev,
        requirementIds: ids,
        added: ids.filter((id) => !prevIds.includes(id)).map((id) => requirements.get(id)!),
        retired: prevIds
          .filter((id) => !ids.includes(id))
          .map((id) => requirements.get(id) ?? toRequirement(byId.get(id)!)),
        runs: [],
        deliveries: [],
        startedAt: row.started_at,
        endedAt: null,
        current: false,
      });
      for (const id of ids) {
        const r = requirements.get(id)!;
        if (r.firstIteration === null) r.firstIteration = no;
      }
      prevIds = ids;
      prevSig = sig;
    }
    const it = started[started.length - 1];
    it.runs.push(row);
    it.status = row.status;
    it.endedAt = row.ended_at;
    it.deliveries.push(...row.deliveries);
  }

  const nowIds = reqs.filter((d) => d.active === 1).map((d) => d.id);
  const nowRev = playbook?.active?.rev ?? null;
  const iterations: Iteration[] = [...started];
  if (!started.length || signature(nowRev, nowIds) !== prevSig) {
    const no = started.length + 1;
    iterations.push({
      no,
      status: "upcoming",
      planRev: nowRev,
      requirementIds: nowIds,
      added: nowIds.filter((id) => !prevIds.includes(id)).map((id) => requirements.get(id)!),
      retired: prevIds
        .filter((id) => !nowIds.includes(id))
        .map((id) => requirements.get(id)!)
        .filter(Boolean),
      runs: [],
      deliveries: [],
      startedAt: null,
      endedAt: null,
      current: false,
    });
    for (const id of nowIds) {
      const r = requirements.get(id)!;
      if (r.firstIteration === null) r.firstIteration = no;
    }
  }

  const openRunNo = runs.filter((r) => OPEN.includes(r.status)).at(-1)?.run_no;
  const openRun = openRunNo !== undefined ? runRows.get(openRunNo)! : null;
  for (const it of iterations) it.deliveries.sort((a, b) => b.delivery.delivered_at - a.delivery.delivered_at);
  const current =
    (openRun && started.find((it) => it.runs.includes(openRun))) ||
    started[started.length - 1] ||
    iterations[iterations.length - 1];
  if (current) current.current = true;

  return {
    iterations: iterations.reverse(),
    requirements: [...requirements.values()].sort((a, b) => b.createdAt - a.createdAt),
    openRun,
    startedCount: started.length,
    nextIterationNo: started.length + 1,
  };
}
