import Link from "next/link";

import { outputsLabel } from "@/components/projects/outputsLabel";
import type { ProjectBoardTask, ProjectDetail } from "@/types";

/**
 * The one sentence that says what the project needs from a person right
 * now, in priority order: a waiting run, blocked cards, outputs to accept,
 * a missing plan/activation — else nothing. Null when the agent has it.
 */
export function nextAction(
  project: ProjectDetail,
  blockedCards: ProjectBoardTask[],
  runnable: boolean,
): { text: string; href: string } | null {
  if (project.archived) return null;
  const waiting = project.runs.find((run) => run.status === "waiting");
  if (waiting) {
    return {
      text: `Run ${waiting.run_no} is waiting for you — continue it.`,
      href: "#panel-runs",
    };
  }
  if (blockedCards.length > 0) {
    return {
      text: `${blockedCards.length} blocked ${blockedCards.length === 1 ? "card needs" : "cards need"} a decision.`,
      href: "#panel-board",
    };
  }
  const awaiting = project.output_rollup?.awaiting_acceptance ?? 0;
  if (awaiting > 0) {
    return {
      text: `${awaiting} delivered ${awaiting === 1 ? "output is" : "outputs are"} waiting for your acceptance.`,
      href: "#panel-outputs",
    };
  }
  if (project.status !== "active") {
    return { text: "Activate the project to let it run.", href: "#panel-settings" };
  }
  if (!runnable) {
    return { text: "Finish the readiness checklist so it can run.", href: "#panel-plan" };
  }
  if (project.runs.some((run) => run.status === "running")) {
    return { text: "A run is in progress — nothing needed from you.", href: "#panel-runs" };
  }
  return null;
}

/**
 * The ladder's headline WITH its label (§9.1) and the card rollup beside it;
 * then blocked cards — a blocked card is the only thing on this page asking
 * for a human right now.
 */
export function ProgressPanel({
  slug,
  project,
  blockedCards,
  runnable = true,
}: {
  slug: string;
  project: ProjectDetail;
  blockedCards: ProjectBoardTask[];
  runnable?: boolean;
}) {
  const { progress, card_rollup: rollup } = project;
  const outputs = outputsLabel(project.output_rollup);
  const action = nextAction(project, blockedCards, runnable);
  return (
    <section
      id="panel-progress"
      data-component="ProgressPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Progress
      </h2>

      {action ? (
        <a
          href={action.href}
          data-component="NextAction"
          className="mt-2 block rounded-lg border border-[var(--color-accent)]/40 bg-[var(--color-surface-2)] px-3 py-2 text-sm"
        >
          <span className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
            Next for you
          </span>
          <span className="block">{action.text}</span>
        </a>
      ) : null}

      <p className="mt-2 text-sm">
        <span className="rounded-full bg-[var(--color-surface-2)] px-2 py-0.5 text-xs text-[var(--color-muted)]">
          {progress.label}
        </span>{" "}
        <span className="font-medium">{progress.headline}</span>
      </p>

      <p className="mt-2 text-xs text-[var(--color-muted)]">
        {rollup.done} of {rollup.total} cards done
        {rollup.running > 0 ? ` · ${rollup.running} running` : ""}
        {rollup.blocked > 0 ? ` · ${rollup.blocked} blocked` : ""}
      </p>
      {outputs ? (
        <p data-component="OutputsRollup" className="mt-1 text-xs text-[var(--color-muted)]">
          {outputs}
        </p>
      ) : null}

      {blockedCards.length > 0 ? (
        <ul className="mt-3 flex flex-col gap-2" data-component="BlockedCards">
          {blockedCards.map((card) => (
            <li key={card.id}>
              <Link
                href={`/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(card.id)}`}
                className="flex items-center gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm"
              >
                <span className="min-w-0 flex-1 truncate">{card.title}</span>
                <span className="text-xs text-red-300">blocked</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
