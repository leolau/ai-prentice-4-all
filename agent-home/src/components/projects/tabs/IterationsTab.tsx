"use client";

import { GuidancePanel } from "@/components/projects/panels/GuidancePanel";
import { RunsPanel } from "@/components/projects/panels/RunsPanel";
import type { ProjectTabProps } from "@/components/projects/tabs/types";

export function IterationsTab({ project, directives }: ProjectTabProps) {
  return (
    <div data-component="IterationsTab" className="flex flex-col gap-4">
      <RunsPanel slug={project.slug} runs={project.runs} archived={project.archived} />
      <GuidancePanel slug={project.slug} initial={directives} archived={project.archived} />
    </div>
  );
}
