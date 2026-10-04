"use client";

import { useMemo } from "react";

import { IterationCard } from "@/components/projects/iterations/IterationCard";
import { RequirementHistory } from "@/components/projects/iterations/RequirementHistory";
import { groupIterations } from "@/components/projects/iterations/groupIterations";
import { useChangesHistory } from "@/components/projects/iterations/useChangesHistory";
import { GuidancePanel } from "@/components/projects/panels/GuidancePanel";
import { RunsPanel } from "@/components/projects/panels/RunsPanel";
import type { ProjectTabProps } from "@/components/projects/tabs/types";

const DISCLOSURE =
  "rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3";

/**
 * The project's story as iterations — one requirement version + plan
 * revision each, newest first — then the requirement history, with the
 * full run list and standing instructions one tap away below.
 */
export function IterationsTab({ project, directives, playbook, onChangeRequest }: ProjectTabProps) {
  const history = useChangesHistory(project.slug, project);
  const grouped = useMemo(
    () => groupIterations(project, directives, playbook, history),
    [project, directives, playbook, history],
  );
  return (
    <div data-component="IterationsTab" className="flex flex-col gap-4">
      <section className="flex flex-col gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">Iterations</h2>
            <p className="text-xs text-[var(--color-muted)]">
              Each iteration is one version of the requirements and plan. Changes apply from the next run.
            </p>
          </div>
          {!project.archived ? (
            <button
              type="button"
              onClick={() => onChangeRequest()}
              className="rounded-xl border border-[var(--color-border)] px-3 py-1.5 text-sm"
            >
              Start a new iteration with changes
            </button>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          {grouped.iterations.map((it) => (
            <IterationCard key={it.no} iteration={it} slug={project.slug} />
          ))}
        </div>
      </section>
      <RequirementHistory requirements={grouped.requirements} />
      <details className={DISCLOSURE}>
        <summary className="cursor-pointer text-sm font-medium">All runs</summary>
        <div className="mt-3">
          <RunsPanel slug={project.slug} runs={project.runs} archived={project.archived} />
        </div>
      </details>
      <details className={DISCLOSURE}>
        <summary className="cursor-pointer text-sm font-medium">Standing instructions</summary>
        <div className="mt-3">
          <GuidancePanel slug={project.slug} initial={directives} archived={project.archived} />
        </div>
      </details>
    </div>
  );
}
