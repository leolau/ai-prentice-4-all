"use client";

import { OutputsPanel } from "@/components/projects/panels/OutputsPanel";
import type { ProjectTabProps } from "@/components/projects/tabs/types";

export function OutputsTab({ project }: ProjectTabProps) {
  return (
    <div data-component="OutputsTab">
      <OutputsPanel slug={project.slug} outputs={project.outputs} archived={project.archived} />
    </div>
  );
}
