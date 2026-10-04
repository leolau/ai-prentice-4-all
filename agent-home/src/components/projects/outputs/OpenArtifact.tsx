"use client";

import type { FileRefTarget } from "@/components/files/FileRefOpener";
import type { ProjectArtifact } from "@/types";

export type OpenFile = (target: FileRefTarget) => Promise<void>;

/**
 * The one Open control for a produced file: a link when the file has an
 * openable URL/route, the shared file opener for a storage `file` ref, and
 * a disabled button (with the reason) when it cannot be reached from here.
 */
export function OpenArtifact({
  file,
  onOpenFile,
  resolving,
  className = "rounded-lg border border-[var(--color-border)] px-2.5 py-1 text-xs",
}: {
  file: Pick<ProjectArtifact, "title" | "href" | "link_kind" | "link_ref">;
  onOpenFile?: OpenFile;
  resolving?: string | null;
  className?: string;
}) {
  const label = `Open ${file.title}`;
  if (file.href) {
    return (
      <a href={file.href} target="_blank" rel="noreferrer" aria-label={label} className={className}>
        Open
      </a>
    );
  }
  if (file.link_kind === "file" && file.link_ref && onOpenFile) {
    const ref = file.link_ref;
    return (
      <button
        type="button"
        aria-label={label}
        onClick={() => void onOpenFile({ ref, label: file.title })}
        disabled={resolving === ref}
        className={`${className} disabled:opacity-50`}
      >
        {resolving === ref ? "Opening…" : "Open"}
      </button>
    );
  }
  return (
    <button
      type="button"
      disabled
      aria-label={label}
      title="This file isn't reachable from here"
      className={`${className} opacity-40`}
    >
      Open
    </button>
  );
}
