import { fileBadge } from "@/components/projects/outputs/artifacts";

/** The DOCX / PDF / MD square on a file tile or row. */
export function FileBadge({ file }: { file: { ext?: string | null; mime?: string | null; title?: string | null } }) {
  return (
    <span
      data-component="FileBadge"
      className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-surface-2)] text-[10px] font-semibold tracking-wide text-[var(--color-muted)]"
    >
      {fileBadge(file)}
    </span>
  );
}
