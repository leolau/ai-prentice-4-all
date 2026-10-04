"use client";

import { useState } from "react";

import { ActionButton } from "@/components/projects/ActionButton";
import { ActionError } from "@/components/projects/outputs/ActionError";
import {
  attachBody,
  shortTitle,
  suggestedOutput,
  unattachedByRun,
  unattachedSentence,
} from "@/components/projects/outputs/artifacts";
import { FileBadge } from "@/components/projects/outputs/FileBadge";
import { OpenArtifact, type OpenFile } from "@/components/projects/outputs/OpenArtifact";
import { useProjectAction } from "@/components/projects/useProjectAction";
import type { ProjectArtifact, ProjectOutputWithDeliveries } from "@/types";

export interface AttachResult {
  delivery_id: string;
  output_id: string;
  by?: string;
}

/**
 * Files a run produced that no declared output owns — the reason a project
 * can read "pending" while the work is sitting right there. Each file gets
 * one Attach action (its own lock) through the existing deliver route.
 */
export function UnattachedWarning({
  slug,
  artifacts,
  outputs,
  archived,
  onAttached,
  onOpenFile,
  resolving,
}: {
  slug: string;
  artifacts: ProjectArtifact[];
  outputs: ProjectOutputWithDeliveries[];
  archived: boolean;
  onAttached: (file: ProjectArtifact, result: AttachResult) => void;
  onOpenFile?: OpenFile;
  resolving?: string | null;
}) {
  const groups = unattachedByRun(artifacts);
  if (groups.length === 0) return null;
  const targets = outputs.filter((o) => o.status !== "accepted" && o.status !== "dropped");
  const suggested = suggestedOutput(outputs);
  return (
    <section
      data-component="UnattachedWarning"
      role="status"
      className="rounded-2xl border border-amber-500/40 bg-amber-500/10 p-4"
    >
      {groups.map((group) => (
        <div key={String(group.runNo)} className="mb-3 last:mb-0">
          <p className="text-sm font-medium text-amber-200">{unattachedSentence(group)}</p>
          <p className="mt-0.5 text-xs text-[var(--color-muted)]">
            Until they are attached, the output they belong to still reads as pending.
          </p>
          <ul className="mt-2 flex flex-col gap-1.5">
            {group.files.map((file) => (
              <AttachRow
                key={file.id}
                slug={slug}
                file={file}
                targets={targets}
                suggestedId={suggested?.id ?? null}
                archived={archived}
                onAttached={onAttached}
                onOpenFile={onOpenFile}
                resolving={resolving}
              />
            ))}
          </ul>
        </div>
      ))}
      {targets.length === 0 && !archived ? (
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          Declare an output below to attach these files to it.
        </p>
      ) : null}
    </section>
  );
}

function AttachRow({
  slug,
  file,
  targets,
  suggestedId,
  archived,
  onAttached,
  onOpenFile,
  resolving,
}: {
  slug: string;
  file: ProjectArtifact;
  targets: ProjectOutputWithDeliveries[];
  suggestedId: string | null;
  archived: boolean;
  onAttached: (file: ProjectArtifact, result: AttachResult) => void;
  onOpenFile?: OpenFile;
  resolving?: string | null;
}) {
  const action = useProjectAction<AttachResult>();
  const [targetId, setTargetId] = useState<string>(
    suggestedId && targets.some((t) => t.id === suggestedId) ? suggestedId : (targets[0]?.id ?? ""),
  );
  const target = targets.find((t) => t.id === targetId) ?? null;
  return (
    <li data-component="AttachRow" className="rounded-lg bg-[var(--color-surface)] px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <FileBadge file={file} />
        <span className="min-w-0 flex-1 truncate text-sm">{file.title}</span>
        <OpenArtifact file={file} onOpenFile={onOpenFile} resolving={resolving} />
      </div>
      {!archived && target ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          {targets.length > 1 ? (
            <select
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
              disabled={action.busy}
              aria-label={`Output for ${file.title}`}
              className="min-w-0 max-w-full flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-2 py-1 text-xs"
            >
              {targets.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </select>
          ) : null}
          <ActionButton
            busy={action.busy}
            pendingLabel="Attaching…"
            onClick={() =>
              void action.run(
                `/api/projects/${encodeURIComponent(slug)}/outputs/${encodeURIComponent(target.id)}/deliver`,
                { body: attachBody(file), onSuccess: (data) => onAttached(file, data) },
              )
            }
            className="rounded-lg border border-amber-400/60 px-3 py-1 text-xs font-medium text-amber-200 disabled:opacity-50"
          >
            Attach to “{shortTitle(target.title)}”
          </ActionButton>
        </div>
      ) : null}
      <ActionError action={action} />
    </li>
  );
}
