"use client";

import { DashActionButton } from "@/components/projects/dashboard/DashActionButton";
import type { NextAction } from "@/components/projects/dashboard/nextAction";
import { TONE_CARD, TONE_DOT } from "@/components/projects/dashboard/tone";
import type { ProjectTab } from "@/components/projects/tabs/types";

/** The one dominant card: what to do next, why, and a one-click way to do it. */
export function NextActionHero({
  slug,
  action,
  onNavigate,
  onChangeRequest,
}: {
  slug: string;
  action: NextAction;
  onNavigate: (tab: ProjectTab) => void;
  onChangeRequest: (initialText?: string) => void;
}) {
  return (
    <section
      data-component="NextActionHero"
      data-state={action.state}
      data-tone={action.tone}
      className={`rounded-2xl border p-4 ${TONE_CARD[action.tone]}`}
    >
      <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
        <span aria-hidden className={`inline-block h-2 w-2 rounded-full ${TONE_DOT[action.tone]}`} />
        {action.needsPerson ? "Next action · for you" : "Next action"}
      </p>
      <h2 className="mt-1.5 text-lg font-semibold leading-snug">{action.headline}</h2>
      <p className="mt-1 text-sm text-[var(--color-muted)]">{action.why}</p>
      <div className="mt-3 flex flex-wrap items-start gap-2">
        <DashActionButton
          key={action.key}
          slug={slug}
          action={action.primary}
          variant="primary"
          onNavigate={onNavigate}
          onChangeRequest={onChangeRequest}
        />
        {action.secondary ? (
          <DashActionButton
            slug={slug}
            action={action.secondary}
            variant="secondary"
            onNavigate={onNavigate}
            onChangeRequest={onChangeRequest}
          />
        ) : null}
      </div>
      <p data-component="NextActionEffect" className="mt-2 text-xs text-[var(--color-muted)]">
        {action.primary.effect}
      </p>
    </section>
  );
}
