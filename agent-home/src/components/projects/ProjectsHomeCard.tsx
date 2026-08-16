import Link from "next/link";

import {
  CADENCE_GLYPH,
  dayDistance,
  HEALTH_LABEL,
} from "@/components/projects/ProjectRow";
import { Pill, type Tone } from "@/components/ui/Pill";
import type { ProjectHealth, ProjectListItem } from "@/types";

const HEALTH_TONE: Record<ProjectHealth, Tone> = {
  ok: "success",
  attention: "warning",
  stalled: "danger",
};

/**
 * The first-class Home card (§13): the active projects with their health and
 * next run, one tap from Home. The fetch stays in the page — this is the
 * pure render, so it can be tested without a principal and the card degrades
 * where the data does.
 */
export function ProjectsHomeCard({ items }: { items: ProjectListItem[] }) {
  return (
    <section
      data-component="ProjectsHomeCard"
      className="mt-4 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <div className="flex items-center justify-between">
        <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
          Projects
        </p>
        <Link
          href="/projects"
          data-component="ProjectsHomeAll"
          className="text-xs text-[var(--color-accent)]"
        >
          All projects ›
        </Link>
      </div>

      {items.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Nothing running right now. When the agent takes on work that lasts —
          a deliverable, a recurring job, a standing duty — it shows up here.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-2">
          {items.map((project) => (
            <li key={project.id}>
              <Link
                href={`/projects/${encodeURIComponent(project.slug)}`}
                className="flex items-center gap-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 active:opacity-70"
              >
                <span aria-hidden className="text-[var(--color-muted)]">
                  {CADENCE_GLYPH[project.cadence]}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">
                    {project.name}
                  </span>
                  <span className="block text-xs text-[var(--color-muted)]">
                    {project.next_run_at != null
                      ? `next ${dayDistance(project.next_run_at)}`
                      : project.schedule ?? project.cadence.replace("_", "-")}
                  </span>
                </span>
                <Pill tone={HEALTH_TONE[project.health]}>
                  {HEALTH_LABEL[project.health]}
                </Pill>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
