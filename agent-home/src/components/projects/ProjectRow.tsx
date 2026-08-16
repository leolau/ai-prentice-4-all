import Link from "next/link";

import { Pill, type Tone } from "@/components/ui/Pill";
import type { ProjectCadence, ProjectHealth, ProjectListItem } from "@/types";

/** §13: the cadence glyph leads the row because it sets what a row promises. */
export const CADENCE_GLYPH: Record<ProjectCadence, string> = {
  one_off: "▣",
  repeatable: "↻",
  standing: "∞",
};

export const CADENCE_LABEL: Record<ProjectCadence, string> = {
  one_off: "one-off",
  repeatable: "repeatable",
  standing: "standing",
};

export const HEALTH_LABEL: Record<ProjectHealth, string> = {
  ok: "ok",
  attention: "attention",
  stalled: "stalled",
};

const HEALTH_TONE: Record<ProjectHealth, Tone> = {
  ok: "success",
  attention: "warning",
  stalled: "danger",
};

/**
 * Epoch seconds → a distance the row can show: "today", "in 3d", "5d ago".
 * The list only ever needs the day grain — a run scheduled for 14:00 is
 * still "today" at 09:00.
 */
export function dayDistance(epochSeconds: number): string {
  const days = Math.round(
    (epochSeconds * 1000 - Date.now()) / 86_400_000,
  );
  if (days === 0) return "today";
  return days > 0 ? `in ${days}d` : `${-days}d ago`;
}

/**
 * One row of the `/projects` list. Pure and server-safe: the cadence glyph,
 * the bold `name`, the dimmed `goal` truncated to one line, then cadence ·
 * when · progress headline · members — everything the backend computes, so
 * the row never re-derives what `progress.headline` already says.
 */
export function ProjectRow({ project }: { project: ProjectListItem }) {
  const when =
    project.cadence === "one_off" && project.due_at != null
      ? `due ${dayDistance(project.due_at)}`
      : project.next_run_at != null
        ? `next ${dayDistance(project.next_run_at)}`
        : null;
  const meta = [
    CADENCE_LABEL[project.cadence],
    when,
    project.progress.headline,
    project.member_count > 0
      ? `${project.member_count} ${project.member_count === 1 ? "member" : "members"}`
      : null,
  ].filter(Boolean);

  return (
    <li
      data-component="ProjectRow"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
    >
      <div className="flex items-center gap-2">
        <span aria-hidden className="text-[var(--color-muted)]">
          {CADENCE_GLYPH[project.cadence]}
        </span>
        <Link
          href={`/projects/${encodeURIComponent(project.slug)}`}
          className="min-w-0 flex-1 truncate text-sm font-semibold"
        >
          {project.name}
        </Link>
        <Pill tone={HEALTH_TONE[project.health]}>
          {HEALTH_LABEL[project.health]}
        </Pill>
      </div>

      {project.goal ? (
        <p className="mt-1 truncate text-sm text-[var(--color-muted)]">
          {project.goal}
        </p>
      ) : null}

      <p className="mt-1 text-xs text-[var(--color-muted)]">
        {meta.join(" · ")}
      </p>
    </li>
  );
}
