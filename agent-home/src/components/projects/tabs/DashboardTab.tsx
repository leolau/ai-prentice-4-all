"use client";

import { OutputsPanel } from "@/components/projects/panels/OutputsPanel";
import { ProgressPanel } from "@/components/projects/panels/ProgressPanel";
import type { ProjectTabProps } from "@/components/projects/tabs/types";

/** Landing tab: what needs you, where it stands, latest outputs. */
export function DashboardTab({ project, board, readiness }: ProjectTabProps) {
  const blockedCards =
    board?.columns
      .flatMap((column) => column.tasks)
      .filter((task) => task.status === "blocked") ?? [];
  return (
    <div data-component="DashboardTab" className="flex flex-col gap-4 md:grid md:grid-cols-2 md:items-start">
      <ProgressPanel
        slug={project.slug}
        project={project}
        blockedCards={blockedCards}
        readiness={readiness}
      />
      <OutputsPanel slug={project.slug} outputs={project.outputs} archived={project.archived} />
    </div>
  );
}
