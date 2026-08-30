import Link from "next/link";

import { CardActions } from "@/components/projects/CardActions";
import { dateTimeLabel, durationLabel } from "@/components/projects/format";
import { Pill, type Tone } from "@/components/ui/Pill";
import type { ProjectCardDetail } from "@/types";

const STATUS_TONE: Record<string, Tone> = {
  todo: "muted",
  triage: "muted",
  running: "accent",
  review: "warning",
  blocked: "danger",
  done: "success",
  archived: "muted",
};

/**
 * One card, read-only (§13): everything the board row knows — stage,
 * assignee, step, the body, the result and the latest worker summary.
 * Card actions (comments, transitions) stay on the board surface.
 */
export function CardDetailView({
  slug,
  card,
}: {
  slug: string;
  card: ProjectCardDetail;
}) {
  const tone = STATUS_TONE[card.status] ?? "muted";
  const timing = [
    `created ${dateTimeLabel(card.created_at)}`,
    card.started_at != null ? `started ${dateTimeLabel(card.started_at)}` : null,
    card.completed_at != null
      ? `completed ${dateTimeLabel(card.completed_at)}`
      : null,
    card.age?.time_to_complete_seconds != null
      ? `took ${durationLabel(card.age.time_to_complete_seconds)}`
      : null,
  ].filter(Boolean);

  return (
    <div data-component="CardDetailView" className="flex flex-col gap-4">
      <header
        data-component="CardHeader"
        className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
      >
        <div className="flex items-center gap-2">
          <h1 className="min-w-0 flex-1 text-lg font-semibold">{card.title}</h1>
          <Pill tone={tone}>{card.status}</Pill>
        </div>
        <p className="mt-1 text-xs text-[var(--color-muted)]">
          {[
            card.assignee ?? "unassigned",
            card.current_step_key ? `step ${card.current_step_key}` : null,
            card.tenant ? `tenant ${card.tenant}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
        <p className="mt-1 text-xs text-[var(--color-muted)]">
          {timing.join(" · ")}
        </p>
        <CardActions slug={slug} taskId={card.id} status={card.status} />
        <Link
          href={`/projects/${encodeURIComponent(slug)}`}
          className="mt-2 inline-block text-xs text-[var(--color-accent)]"
        >
          ‹ Back to the project
        </Link>
      </header>

      {card.body ? (
        <section
          data-component="CardBody"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
        >
          <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
            Brief
          </h2>
          <p className="mt-2 whitespace-pre-wrap text-sm">{card.body}</p>
        </section>
      ) : null}

      {card.latest_summary || card.result ? (
        <section
          data-component="CardResult"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
        >
          <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
            Latest from the worker
          </h2>
          <p className="mt-2 whitespace-pre-wrap text-sm">
            {card.latest_summary ?? card.result}
          </p>
        </section>
      ) : null}
    </div>
  );
}
