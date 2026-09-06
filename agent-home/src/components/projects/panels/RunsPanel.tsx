"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { friendlyError } from "@/components/projects/errors";

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

const LIVE_HINT: Partial<Record<ProjectRunStatus, string>> = {
  running: "working now",
  waiting: "waiting for you",
  blocked: "blocked — needs attention",
};

export function isLiveRun(status: ProjectRunStatus): boolean {
  return status === "running" || status === "waiting" || status === "blocked";
}

/**
 * The last runs — the record of what the project actually did. Tap a row for
 * the run page: its cards, deliveries, retro and score (§7). Live rows carry
 * Continue / Cancel / Stop inline so a run can be steered without opening
 * it; the semantics mirror the run page (Cancel lets a running card finish,
 * Stop terminates it).
 */
export function RunsPanel({
  slug,
  runs,
  archived = false,
}: {
  slug: string;
  runs: ProjectRunBrief[];
  archived?: boolean;
}) {
  const router = useRouter();
  const [busyRun, setBusyRun] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const post = async (runNo: number, action: "continue" | "cancel" | "stop") => {
    setBusyRun(runNo);
    setError(null);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/runs/${runNo}/${action}`,
        { method: "POST" },
      );
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) {
        setError(friendlyError({ status: res.status, detail: data.detail }, "That did not go through."));
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusyRun(null);
    }
  };

  const actionClass =
    "rounded-lg border border-[var(--color-border)] px-2.5 py-1 text-xs disabled:opacity-50";

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
          No runs yet. When the readiness checklist at the top is all green,
          press <strong>Run now</strong> in the header (or set a schedule in
          Settings) — each run lands here with its outcome.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1.5">
          {runs.map((run) => {
            const live = isLiveRun(run.status);
            const busy = busyRun === run.run_no;
            return (
              <li
                key={run.run_no}
                data-component="RunRow"
                className="rounded-lg bg-[var(--color-surface-2)]"
              >
                <Link
                  href={`/projects/${encodeURIComponent(slug)}/runs/${run.run_no}`}
                  className="flex items-center gap-2 px-3 py-2 text-sm active:opacity-70"
                >
                  <span className="font-medium">#{run.run_no}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-muted)]">
                    {run.trigger} · {dateTimeLabel(run.started_at)} ·{" "}
                    {live
                      ? `${LIVE_HINT[run.status]} · ${agoLabel(run.started_at)}`
                      : durationLabel(run.duration_seconds)}
                    {run.outcome ? ` · ${run.outcome}` : ""}
                    {run.score_user != null ? ` · ${run.score_user}/5` : ""}
                    {run.score_self != null
                      ? ` · self ${run.score_self}/5 ⚠ diverges`
                      : ""}
                  </span>
                  <Pill tone={RUN_TONE[run.status]}>{run.status}</Pill>
                </Link>
                {live && !archived ? (
                  <div
                    data-component="RunRowActions"
                    className="flex flex-wrap items-center gap-1.5 px-3 pb-2"
                  >
                    {run.status === "waiting" ? (
                      <button
                        type="button"
                        onClick={() => void post(run.run_no, "continue")}
                        disabled={busy}
                        className="rounded-lg bg-[var(--color-accent)] px-2.5 py-1 text-xs font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                      >
                        Continue
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => void post(run.run_no, "cancel")}
                      disabled={busy}
                      title="Stop promoting new work; a running card finishes."
                      className={actionClass}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (
                          !window.confirm(
                            `Stop run ${run.run_no} now? Work in progress is terminated where it stands — Cancel instead lets a running card finish.`,
                          )
                        ) {
                          return;
                        }
                        void post(run.run_no, "stop");
                      }}
                      disabled={busy}
                      className={`${actionClass} text-red-400`}
                    >
                      Stop now
                    </button>
                    {busy ? (
                      <span className="text-xs text-[var(--color-muted)]">
                        Working…
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      {error ? (
        <p role="alert" className="mt-2 text-sm text-red-400">
          {error}
        </p>
      ) : null}

      {runs.length > 0 ? (
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          newest run {agoLabel(runs[0].started_at)}
        </p>
      ) : null}
    </section>
  );
}
