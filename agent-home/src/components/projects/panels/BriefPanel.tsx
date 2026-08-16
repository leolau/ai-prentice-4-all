import type { ProjectDetail } from "@/types";

/**
 * The only panel that says what this project is: the goal sentence lives in
 * the header (it is a field, not a summary), so this carries the
 * requirements and the audience.
 */
export function BriefPanel({ project }: { project: ProjectDetail }) {
  return (
    <section
      id="panel-brief"
      data-component="BriefPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Brief
      </h2>
      {project.description ? (
        <details className="mt-2 group">
          <summary className="cursor-pointer list-none whitespace-pre-wrap text-sm text-[var(--color-text)]">
            <span className="line-clamp-[12] group-open:line-clamp-none">
              {project.description}
            </span>
          </summary>
        </details>
      ) : (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          No requirements yet — the lead can write what &ldquo;done&rdquo;
          should look like.
        </p>
      )}
      {project.target_audience ? (
        <span className="mt-3 inline-block rounded-full bg-[var(--color-surface-2)] px-2 py-1 text-xs">
          for {project.target_audience}
        </span>
      ) : null}
    </section>
  );
}
