"use client";

import { FilesPanel } from "@/components/projects/panels/FilesPanel";
import { MemoriesPanel } from "@/components/projects/panels/MemoriesPanel";
import { ReferencesPanel } from "@/components/projects/panels/ReferencesPanel";
import type { ProjectTabProps } from "@/components/projects/tabs/types";

export function InputsTab({ project }: ProjectTabProps) {
  return (
    <div data-component="InputsTab" className="flex flex-col gap-4">
      <FilesPanel project={project} archived={project.archived} />
      <MemoriesPanel project={project} />
      <ReferencesPanel project={project} />
    </div>
  );
}
