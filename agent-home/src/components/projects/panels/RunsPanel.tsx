import Link from "next/link";

import {
  agoLabel,
  dateTimeLabel,
  durationLabel,
} from "@/components/projects/format";
import { Pill } from "@/components/ui/Pill";
import type { ProjectRunBrief, ProjectRunStatus } from "@/types";

const RUN_TONE: Record<
  ProjectRunStatus,
  "muted" | "accent" | "success" | "warning" | "danger"
> = {
  running: "accent",
  waiting: "warning",
  blocked: "danger",
  done: "success",
  failed: "danger",
  cancelled: "muted",
};

/**
 * The last runs — the record of what the project actually did. Tap a row for
 * the run page: its cards, deliveries, retro and score (§7).
 */
export function RunsPanel({
  slug,
  runs,
}: {
  slug: string;
  runs: ProjectRunBrief[];
}) {
  return (
    <section
      id="panel-runs"
      data-component="RunsPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Runs
      </h2>

      {runs.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          No runs yet — the first schedule fire or a manual run will appear
          here with its outcome.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1.5">
          {runs.map((run) => (
            <li key={run.run_no}>
              <Link
                href={`/projects/${encodeURIComponent(slug)}/runs/${run.run_no}`}
                data-component="RunRow"
                className="flex items-center gap-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm active:opacity-70"
              >
                <span className="font-medium">#{run.run_no}</span>
                <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-muted)]">
                  {run.trigger} · {dateTimeLabel(run.started_at)} ·{" "}
                  {durationLabel(run.duration_seconds)}
                  {run.outcome ? ` · ${run.outcome}` : ""}
                  {run.score_user != null ? ` · ${run.score_user}/5` : ""}
                </span>
                <Pill tone={RUN_TONE[run.status]}>{run.status}</Pill>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {runs.length > 0 ? (
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          newest run {agoLabel(runs[0].started_at)}
        </p>
      ) : null}
    </section>
  );
}
