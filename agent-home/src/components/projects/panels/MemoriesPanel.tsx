import { LinkRow } from "@/components/projects/panels/LinkRow";
import type { ProjectDetail } from "@/types";

/**
 * Linked memories (§11). Like References, this panel hides entirely when
 * empty — memory links are the other kind that is often genuinely
 * irrelevant (§13).
 */
export function MemoriesPanel({ project }: { project: ProjectDetail }) {
  const memories = project.links.memory ?? [];
  if (memories.length === 0) {
    return null;
  }
  return (
    <section
      id="panel-memories"
      data-component="MemoriesPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Memories
      </h2>
      <ul className="mt-2 flex flex-col gap-1.5">
        {memories.map((link) => (
          <li key={`${link.profile}:${link.ref}`}>
            <LinkRow link={link} />
          </li>
        ))}
      </ul>
    </section>
  );
}
