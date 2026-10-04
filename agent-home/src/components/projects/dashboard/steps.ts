import type { DashIntent } from "@/components/projects/dashboard/nextAction";

export interface MutationStep {
  path: string;
  method: "POST" | "PATCH";
  body?: unknown;
}

/**
 * The HTTP requests one Dashboard mutation sends, in order. Navigation
 * intents return `[]`. "Approve N cards & resume work" is N card PATCHes to
 * Ready and then, only when no run is open, one POST that starts a run.
 */
export function mutationSteps(slug: string, intent: DashIntent): MutationStep[] {
  const base = `/api/projects/${encodeURIComponent(slug)}`;
  switch (intent.kind) {
    case "continue_run":
      return [{ path: `${base}/runs/${intent.runNo}/continue`, method: "POST" }];
    case "resume_run":
      return [{ path: `${base}/runs/${intent.runNo}/resume`, method: "POST" }];
    case "start_run":
      return [{ path: `${base}/runs`, method: "POST" }];
    case "unblock_card":
      return [
        {
          path: `${base}/cards/${encodeURIComponent(intent.taskId)}`,
          method: "PATCH",
          body: { status: "ready" },
        },
      ];
    case "activate":
      return [{ path: base, method: "PATCH", body: { status: "active" } }];
    case "mark_done":
      return [{ path: base, method: "PATCH", body: { status: "done" } }];
    case "approve_cards": {
      const steps: MutationStep[] = intent.taskIds.map((id) => ({
        path: `${base}/cards/${encodeURIComponent(id)}`,
        method: "PATCH",
        body: { status: "ready" },
      }));
      if (intent.startRun) steps.push({ path: `${base}/runs`, method: "POST" });
      return steps;
    }
    default:
      return [];
  }
}

/** What the person sees once the server confirmed, before the refresh lands. */
export function confirmedText(intent: DashIntent, data: unknown): string {
  const runNo = (() => {
    const d = data as { run?: { run_no?: unknown }; run_no?: unknown } | null;
    const n = d?.run?.run_no ?? d?.run_no;
    return typeof n === "number" ? n : null;
  })();
  switch (intent.kind) {
    case "continue_run":
      return `Run ${intent.runNo} is going again.`;
    case "resume_run":
      return `Run ${intent.runNo} resumed.`;
    case "start_run":
      return runNo != null ? `Run ${runNo} started.` : "Run started.";
    case "unblock_card":
      return "Card is back in Ready.";
    case "activate":
      return "Project is active.";
    case "mark_done":
      return "Project marked done.";
    case "approve_cards": {
      const n = intent.taskIds.length;
      const approved = `Approved ${n} ${n === 1 ? "card" : "cards"}.`;
      if (!intent.startRun) return approved;
      return runNo != null ? `${approved} Run ${runNo} started.` : `${approved} Run started.`;
    }
    default:
      return "Done.";
  }
}
