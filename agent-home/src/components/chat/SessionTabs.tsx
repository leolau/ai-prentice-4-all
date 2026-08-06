"use client";

import type { SessionSummary } from "@/types";

const NEW_KEY = "__new__";

function titleOf(s: SessionSummary): string {
  return s.title || s.preview || "Untitled";
}

/**
 * A left-right scrollable strip of the principal's conversations at the top of
 * the chat page (replaces the dropdown switcher). Tapping a non-active chip
 * switches to that conversation; tapping the *active* chip opens its details
 * popup (rename + stats). A pulsing dot marks a conversation with a live turn.
 */
export function SessionTabs({
  sessions,
  activeId,
  busyKeys,
  onSelect,
  onOpenDetails,
  onNew,
  onOpenArchived,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  busyKeys: string[];
  onSelect: (id: string) => void;
  onOpenDetails: (session: SessionSummary) => void;
  onNew: () => void;
  onOpenArchived: () => void;
}) {
  // A brand-new conversation has no persisted row yet; show it as an active,
  // non-editable chip so the strip reflects what's on screen.
  const showNew = activeId === null;
  return (
    <div
      data-component="SessionTabs"
      className="mb-3 flex items-center gap-2"
    >
      <div className="flex min-w-0 flex-1 gap-2 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:thin]">
        {showNew ? (
          <span className="flex shrink-0 items-center gap-1.5 rounded-xl border border-[var(--color-accent)] bg-[var(--color-surface-2)] px-3 py-2 text-sm font-medium text-[var(--color-fg)]">
            New conversation
            {busyKeys.includes(NEW_KEY) ? (
              <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--color-accent)]" />
            ) : null}
          </span>
        ) : null}
        {sessions.map((s) => {
          const active = s.id === activeId;
          const busy = busyKeys.includes(s.id);
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => (active ? onOpenDetails(s) : onSelect(s.id))}
              aria-label={
                active
                  ? `Edit conversation "${titleOf(s)}"`
                  : `Switch to conversation "${titleOf(s)}"`
              }
              className={`flex shrink-0 items-center gap-1.5 rounded-xl border px-3 py-2 text-sm font-medium ${
                active
                  ? "border-[var(--color-accent)] bg-[var(--color-surface-2)] text-[var(--color-fg)]"
                  : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-muted)]"
              }`}
            >
              <span className="max-w-[9rem] truncate">{titleOf(s)}</span>
              {busy ? (
                <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--color-accent)]" />
              ) : null}
              {active ? (
                <span aria-hidden="true" className="text-xs opacity-70">
                  ✎
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      <button
        type="button"
        onClick={onOpenArchived}
        aria-label="Show archived conversations"
        title="Archived conversations"
        className="shrink-0 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-muted)]"
      >
        Archived
      </button>
      <button
        type="button"
        onClick={onNew}
        aria-label="New conversation"
        className="shrink-0 rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-[var(--color-accent-fg)]"
      >
        + New
      </button>
    </div>
  );
}
