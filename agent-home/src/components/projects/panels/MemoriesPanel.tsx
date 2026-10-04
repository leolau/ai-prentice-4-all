"use client";

import { useMemo, useState } from "react";

import { AddMemoryButton } from "@/components/projects/inputs/AddMemoryButton";
import { InputRow } from "@/components/projects/inputs/InputRow";
import {
  groupInputLinks,
  suggestionQuery,
  upsertLink,
  withoutLink,
} from "@/components/projects/inputs/inputKinds";
import { MemoryPicker } from "@/components/projects/inputs/MemoryPicker";
import { useServerState } from "@/components/ui/useRefresh";
import type { ProjectDetail, ProjectLink } from "@/types";

/**
 * Memories linked to the project (§11): things the agent already knows that
 * this project should use. Never hides — an empty panel asks for them, and
 * "Add memory" opens a search over the caller's memories, seeded with the
 * goal.
 */
export function MemoriesPanel({
  project,
  archived = false,
  callerUserId,
  startOpen = false,
  fetchImpl,
}: {
  project: ProjectDetail;
  archived?: boolean;
  callerUserId?: string;
  /** Open the search straight away (the empty Inputs tab). */
  startOpen?: boolean;
  /** Test seam for the memory search. */
  fetchImpl?: typeof fetch;
}) {
  const fromServer = useMemo(() => groupInputLinks(project.links).memories, [project.links]);
  const [memories, setMemories] = useServerState<ProjectLink[]>(fromServer);
  const [open, setOpen] = useState(startOpen);
  const linked = new Set(memories.map((m) => m.ref));
  const profile = project.host_profile ?? "default";

  return (
    <section
      id="panel-memories"
      data-component="MemoriesPanel"
      className="flex flex-col gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">🧠 Memory</h2>
          <p className="text-xs text-[var(--color-muted)]">Things the agent already knows</p>
        </div>
        {archived || open ? null : (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)]"
          >
            Add memory
          </button>
        )}
      </div>

      {memories.length === 0 ? (
        <p className="text-sm text-[var(--color-muted)]">
          No memories linked yet. Search your memory for anything this project
          should know — a company, a person, a past agreement.
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {memories.map((link) => (
            <InputRow
              key={`${link.profile}:${link.ref}`}
              slug={project.slug}
              link={link}
              icon="🧠"
              callerUserId={callerUserId}
              archived={archived}
              onRemoved={(gone) => setMemories((prev) => withoutLink(prev, gone))}
            />
          ))}
        </ul>
      )}

      {!archived && open ? (
        <div className="flex flex-col gap-2 rounded-xl bg-[var(--color-surface-2)] p-2">
          <MemoryPicker
            suggestFrom={suggestionQuery(project.goal ?? "", project.description ?? "")}
            fetchImpl={fetchImpl}
            renderAction={(row) => (
              <AddMemoryButton
                slug={project.slug}
                row={row}
                projectId={project.id}
                profile={profile}
                added={linked.has(row.id)}
                onAdded={(link) => setMemories((prev) => upsertLink(prev, link))}
              />
            )}
          />
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="self-end text-xs text-[var(--color-muted)] underline"
          >
            Done
          </button>
        </div>
      ) : null}
    </section>
  );
}
