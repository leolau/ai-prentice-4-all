import { LinkRow } from "@/components/projects/panels/LinkRow";
import type { ProjectDetail } from "@/types";

/**
 * Linked /files assets (§11.1). Card attachments join this grid once the
 * board read carries them; today the panel renders what the links store
 * knows. Empty collapses to a single "Add …" affordance rather than
 * disappearing (§13).
 */
export function FilesPanel({ project }: { project: ProjectDetail }) {
  const files = project.links.file ?? [];
  return (
    <section
      id="panel-files"
      data-component="FilesPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Files
      </h2>
      {files.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Add a file — anything the project reads or produced belongs here.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1.5">
          {files.map((link) => (
            <li key={`${link.profile}:${link.ref}`}>
              <LinkRow link={link} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
