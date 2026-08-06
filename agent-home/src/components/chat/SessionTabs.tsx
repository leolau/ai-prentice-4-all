"use client";

import { useState } from "react";

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
  onReorder,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  busyKeys: string[];
  onSelect: (id: string) => void;
  onOpenDetails: (session: SessionSummary) => void;
  onNew: () => void;
  onOpenArchived: () => void;
  /** Commit a new left-to-right ordering of the session ids (drag-to-reorder). */
  onReorder: (orderedIds: string[]) => void;
}) {
  // A brand-new conversation has no persisted row yet (it only gets one when
  // its first turn completes). Show it as a chip whenever it is on screen OR
  // has a live turn running — otherwise switching away mid-turn would make the
  // new conversation vanish from the strip until the turn finishes.
  const newActive = activeId === null;
  const newBusy = busyKeys.includes(NEW_KEY);
  const showNew = newActive || newBusy;
  // Index of the chip currently being dragged, and the one it is hovering over,
  // so we can show a drop indicator without mutating state until the drop.
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);

  function drop(from: number, to: number) {
    setDragIndex(null);
    setOverIndex(null);
    if (from === to) return;
    const ids = sessions.map((s) => s.id);
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    onReorder(ids);
  }

  return (
    <div
      data-component="SessionTabs"
      className="mb-3 flex items-center gap-2"
    >
      <div className="flex min-w-0 flex-1 gap-2 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:thin]">
        {showNew ? (
          newActive ? (
            <span className="flex shrink-0 items-center gap-1.5 rounded-xl border border-[var(--color-accent)] bg-[var(--color-surface-2)] px-3 py-2 text-sm font-medium text-[var(--color-fg)]">
              New conversation
              {newBusy ? (
                <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--color-accent)]" />
              ) : null}
            </span>
          ) : (
            <button
              type="button"
              onClick={onNew}
              aria-label="Switch to the new conversation"
              className="flex shrink-0 items-center gap-1.5 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm font-medium text-[var(--color-muted)]"
            >
              New conversation
              <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--color-accent)]" />
            </button>
          )
        ) : null}
        {sessions.map((s, index) => {
          const active = s.id === activeId;
          const busy = busyKeys.includes(s.id);
          const dragging = dragIndex === index;
          const dropTarget = overIndex === index && dragIndex !== index;
          return (
            <button
              key={s.id}
              type="button"
              draggable
              onClick={() => (active ? onOpenDetails(s) : onSelect(s.id))}
              onDragStart={() => setDragIndex(index)}
              onDragOver={(e) => {
                e.preventDefault();
                if (overIndex !== index) setOverIndex(index);
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (dragIndex !== null) drop(dragIndex, index);
              }}
              onDragEnd={() => {
                setDragIndex(null);
                setOverIndex(null);
              }}
              aria-label={
                active
                  ? `Edit conversation "${titleOf(s)}"`
                  : `Switch to conversation "${titleOf(s)}"`
              }
              title="Drag to reorder"
              className={`flex shrink-0 cursor-grab items-center gap-1.5 rounded-xl border px-3 py-2 text-sm font-medium active:cursor-grabbing ${
                active
                  ? "border-[var(--color-accent)] bg-[var(--color-surface-2)] text-[var(--color-fg)]"
                  : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-muted)]"
              } ${dragging ? "opacity-40" : ""} ${
                dropTarget ? "ring-2 ring-[var(--color-accent)]" : ""
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
