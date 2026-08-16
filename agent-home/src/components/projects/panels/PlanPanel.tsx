import { dateTimeLabel } from "@/components/projects/format";
import type { ProjectPlaybookResponse } from "@/types";

/**
 * The active playbook, labelled **Plan** — "playbook" is our word, not the
 * user's (§13). The prose plan plus the steps as an indented list (not a
 * graph widget), its revision and who activated it. Proposed revisions and
 * their diffs land with step 10.
 */
export function PlanPanel({
  playbook,
}: {
  playbook: ProjectPlaybookResponse | null;
}) {
  const active = playbook?.active ?? null;
  return (
    <section
      id="panel-plan"
      data-component="PlanPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Plan
      </h2>

      {playbook == null ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          The plan is unavailable right now.
        </p>
      ) : active == null ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          No active plan — runs start from cards alone until a playbook is
          saved and activated.
        </p>
      ) : (
        <>
          {active.body ? (
            <p className="mt-2 whitespace-pre-wrap text-sm">
              {active.body}
            </p>
          ) : null}
          {active.steps && active.steps.length > 0 ? (
            <ol className="mt-2 flex flex-col gap-1">
              {active.steps.map((step, index) => (
                <li
                  key={step.key}
                  className="flex items-baseline gap-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-1.5 text-sm"
                  style={{ marginLeft: `${(step.needs?.length ?? 0) > 0 ? 1 : 0}rem` }}
                >
                  <span className="text-xs text-[var(--color-muted)]">
                    {index + 1}.
                  </span>
                  <span className="min-w-0 flex-1">
                    {step.title}
                    {step.checkpoint ? (
                      <span className="ml-2 rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-300">
                        checkpoint
                      </span>
                    ) : null}
                    {step.assignee ? (
                      <span className="ml-2 text-xs text-[var(--color-muted)]">
                        → {step.assignee}
                      </span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ol>
          ) : null}
          <p className="mt-2 text-xs text-[var(--color-muted)]">
            revision {active.rev}
            {active.note ? ` · ${active.note}` : ""}
            {active.activated_at != null
              ? ` · activated ${dateTimeLabel(active.activated_at)}`
              : ""}
            {active.created_by ? ` · by ${active.created_by}` : ""}
          </p>
        </>
      )}
    </section>
  );
}
