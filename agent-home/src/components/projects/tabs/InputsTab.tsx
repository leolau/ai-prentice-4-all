"use client";

import { groupInputLinks, inputCount } from "@/components/projects/inputs/inputKinds";
import { FilesPanel } from "@/components/projects/panels/FilesPanel";
import { MemoriesPanel } from "@/components/projects/panels/MemoriesPanel";
import { ReferencesPanel } from "@/components/projects/panels/ReferencesPanel";
import type { ProjectTabProps } from "@/components/projects/tabs/types";

/**
 * Everything the agent works from, in one place: files (templates and
 * references), memories, and links & notes. With nothing attached yet the
 * tab asks for inputs, like step 2 of the new-project wizard.
 */
export function InputsTab({ project, callerUserId }: ProjectTabProps) {
  const empty = inputCount(groupInputLinks(project.links)) === 0;
  const archived = project.archived;
  return (
    <div data-component="InputsTab" className="flex flex-col gap-4">
      {empty && !archived ? (
        <div
          data-component="InputsEmptyState"
          className="rounded-2xl border border-dashed border-[var(--color-accent)] bg-[var(--color-surface)] p-4"
        >
          <h2 className="text-base font-semibold">Do you have anything the agent should use?</h2>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Projects with source material get it right first time. Add
            templates, earlier agreements, notes, or things you&rsquo;ve told
            the agent before.
          </p>
        </div>
      ) : null}
      <FilesPanel project={project} archived={archived} callerUserId={callerUserId} />
      <MemoriesPanel
        project={project}
        archived={archived}
        callerUserId={callerUserId}
        startOpen={empty && !archived}
      />
      <ReferencesPanel project={project} archived={archived} callerUserId={callerUserId} />
    </div>
  );
}
