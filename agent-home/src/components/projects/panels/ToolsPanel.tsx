import type { ProjectDetail } from "@/types";

function splitCsv(value: string | null): string[] {
  if (!value) {
    return [];
  }
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function ChipGroup({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="mt-2">
      <p className="text-xs text-[var(--color-muted)]">{label}</p>
      <ul className="mt-1 flex flex-wrap gap-1.5">
        {items.map((item) => (
          <li
            key={item}
            className="rounded-full bg-[var(--color-surface-2)] px-2.5 py-1 text-xs"
          >
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Tool narrowing (§4.1): CSV filters intersected with the host profile at
 * spawn. Read-only here — empty means "the full host toolset", so the panel
 * says that plainly instead of collapsing.
 */
export function ToolsPanel({ project }: { project: ProjectDetail }) {
  const toolsets = splitCsv(project.toolsets);
  const skills = splitCsv(project.skills);
  return (
    <section
      id="panel-tools"
      data-component="ToolsPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Tools
      </h2>
      {toolsets.length === 0 && skills.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          No narrowing — runs inherit the full host profile toolset.
        </p>
      ) : (
        <>
          {toolsets.length > 0 ? (
            <ChipGroup label="Toolsets" items={toolsets} />
          ) : null}
          {skills.length > 0 ? <ChipGroup label="Skills" items={skills} /> : null}
        </>
      )}
      {project.host_profile ? (
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          Host profile: {project.host_profile} — narrowing is intersected with
          it at spawn (§4.1).
        </p>
      ) : null}
    </section>
  );
}
