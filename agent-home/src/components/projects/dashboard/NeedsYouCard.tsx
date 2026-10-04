"use client";

import { DashActionButton } from "@/components/projects/dashboard/DashActionButton";
import type { NeedsYouItem } from "@/components/projects/dashboard/nextAction";
import { TONE_DOT } from "@/components/projects/dashboard/tone";
import type { ProjectTab } from "@/components/projects/tabs/types";

/**
 * Everything waiting on a person, ranked. The item the hero already offers
 * points up to it instead of repeating its button, so one mutation is never
 * reachable from two independently-locked buttons.
 */
export function NeedsYouCard({
  slug,
  items,
  heroKey,
  onNavigate,
  onChangeRequest,
}: {
  slug: string;
  items: NeedsYouItem[];
  heroKey: string;
  onNavigate: (tab: ProjectTab) => void;
  onChangeRequest: (initialText?: string) => void;
}) {
  return (
    <section
      data-component="NeedsYou"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Needs you</h2>
        <span
          data-component="NeedsYouCount"
          className={`rounded-full px-2 py-0.5 text-xs tabular-nums ${
            items.length > 0
              ? "bg-amber-500/20 text-amber-300"
              : "bg-[var(--color-surface-2)] text-[var(--color-muted)]"
          }`}
        >
          {items.length}
        </span>
        <button
          type="button"
          onClick={() => onNavigate("board")}
          className="ml-auto text-xs text-[var(--color-accent)] underline-offset-2 hover:underline"
        >
          Open board ›
        </button>
      </div>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Nothing right now. The agent has it.
        </p>
      ) : (
        <ol className="mt-2 flex flex-col divide-y divide-[var(--color-border)]">
          {items.map((item) => (
            <li
              key={item.key}
              data-needs-you={item.key}
              className="flex items-start gap-3 py-2.5"
            >
              <span
                aria-hidden
                className={`mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${TONE_DOT[item.tone]}`}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{item.title}</span>
                <span className="block text-xs text-[var(--color-muted)]">{item.detail}</span>
              </span>
              {item.key === heroKey ? (
                <span className="shrink-0 rounded-full bg-[var(--color-surface-2)] px-2 py-0.5 text-xs text-[var(--color-muted)]">
                  ↑ above
                </span>
              ) : (
                <DashActionButton
                  slug={slug}
                  action={item.action}
                  variant="row"
                  onNavigate={onNavigate}
                  onChangeRequest={onChangeRequest}
                />
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
