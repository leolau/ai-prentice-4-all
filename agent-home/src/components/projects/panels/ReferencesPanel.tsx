import { LinkRow } from "@/components/projects/panels/LinkRow";
import type { ProjectDetail, ProjectLink } from "@/types";

/**
 * References panel (§13): samples sit above a rule, then references and
 * urls. Unlike most panels this one hides entirely when empty — references
 * are the link kind most often genuinely irrelevant (§13).
 */
export function ReferencesPanel({ project }: { project: ProjectDetail }) {
  const samples = project.links.sample ?? [];
  const references = [
    ...(project.links.reference ?? []),
    ...(project.links.url ?? []),
  ];
  if (samples.length === 0 && references.length === 0) {
    return null;
  }

  const renderRows = (links: ProjectLink[]) => (
    <ul className="mt-2 flex flex-col gap-1.5">
      {links.map((link) => (
        <li key={`${link.profile}:${link.ref}`}>
          <LinkRow link={link} />
        </li>
      ))}
    </ul>
  );

  return (
    <section
      id="panel-references"
      data-component="ReferencesPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        References
      </h2>
      {samples.length > 0 ? (
        <>
          <p className="mt-2 text-xs text-[var(--color-muted)]">Samples</p>
          {renderRows(samples)}
        </>
      ) : null}
      {references.length > 0 ? (
        <>
          {samples.length > 0 ? (
            <hr className="my-3 border-[var(--color-border)]" />
          ) : null}
          {renderRows(references)}
        </>
      ) : null}
    </section>
  );
}
