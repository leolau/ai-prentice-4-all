"use client";

import { useMemo, useState } from "react";

import {
  categorizeSession,
  CHAT_CATEGORY_LABELS,
  CHAT_CATEGORY_ORDER,
  groupSessionsByCategory,
  type ChatCategory,
} from "@/lib/chat/categorize";
import type { SessionSummary } from "@/types";

const NEW_KEY = "__new__";

function titleOf(s: SessionSummary): string {
  return s.title || s.preview || "Untitled";
}

/**
 * The principal's conversations at the top of the chat page, grouped into
 * labelled rows — Kanban / Daily / Scheduled / Others (see
 * `lib/chat/categorize.ts`) — each independently horizontally scrollable.
 * Tapping a non-active chip switches to that conversation; tapping the
 * *active* chip opens its details popup (rename + stats). A pulsing dot
 * marks a conversation with a live turn.
 *
 * Replaces a single flat strip that showed every conversation in one
 * undifferentiated row (and, briefly, a separate "All conversations" modal
 * for grouped browsing) — both made it harder to tell what kind of
 * conversation you were looking at, and the modal added a click just to see
 * grouping that now shows up immediately.
 *
 * Drag-to-reorder is scoped to *within* a category's row: dragging changes
 * that category's relative order among itself, then the new global id order
 * is rebuilt by splicing the reordered ids back into their original slots
 * (every other category's ids keep their exact positions). Reordering
 * across categories isn't supported — the categories are derived from the
 * session's own data, not a manual grouping, so there's nothing to move it
 * *into*.
 */
export function SessionTabs({
  sessions,
  activeId,
  busyKeys,
  onSelect,
  onOpenDetails,
  onNew,
  onReorder,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  busyKeys: string[];
  onSelect: (id: string) => void;
  onOpenDetails: (session: SessionSummary) => void;
  /** Switch back to the not-yet-persisted "New conversation" (used by the busy chip). */
  onNew: () => void;
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

  // Categorized once per render of the same session list, so a drag
  // interaction and its resulting reorder-rebuild agree on categories even
  // right at a local-day boundary.
  const now = useMemo(() => new Date(), [sessions]);
  const groups = useMemo(() => groupSessionsByCategory(sessions, now), [sessions, now]);

  // Drag state: which category's row, and which index within it.
  const [dragCategory, setDragCategory] = useState<ChatCategory | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);

  function dropWithinCategory(category: ChatCategory, from: number, to: number) {
    setDragCategory(null);
    setDragIndex(null);
    setOverIndex(null);
    if (from === to) return;
    const catIds = groups[category].map((s) => s.id);
    const [moved] = catIds.splice(from, 1);
    catIds.splice(to, 0, moved);
    // Rebuild the full id order: walk the original order, replacing this
    // category's ids with their new local order and leaving every other
    // id exactly where it was.
    let catPos = 0;
    const newOrder = sessions.map((s) => {
      if (categorizeSession(s, now) !== category) return s.id;
      const id = catIds[catPos];
      catPos += 1;
      return id;
    });
    onReorder(newOrder);
  }

  return (
    <div data-component="SessionTabs" className="mb-3 flex flex-col gap-2">
      {showNew ? (
        <div className="flex gap-2">
          {newActive ? (
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
          )}
        </div>
      ) : null}
      {CHAT_CATEGORY_ORDER.map((category) => {
        const items = groups[category];
        if (items.length === 0) return null;
        return (
          <div key={category} data-category={category}>
            <p className="mb-1 px-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              {CHAT_CATEGORY_LABELS[category]}
            </p>
            <div className="flex gap-2 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:thin]">
              {items.map((s, index) => {
                const active = s.id === activeId;
                const busy = busyKeys.includes(s.id);
                const dragging = dragCategory === category && dragIndex === index;
                const dropTarget =
                  dragCategory === category &&
                  overIndex === index &&
                  dragIndex !== index;
                return (
                  <button
                    key={s.id}
                    type="button"
                    draggable
                    onClick={() => (active ? onOpenDetails(s) : onSelect(s.id))}
                    onDragStart={() => {
                      setDragCategory(category);
                      setDragIndex(index);
                    }}
                    onDragOver={(e) => {
                      e.preventDefault();
                      if (dragCategory !== category) return;
                      if (overIndex !== index) setOverIndex(index);
                    }}
                    onDrop={(e) => {
                      e.preventDefault();
                      if (dragCategory === category && dragIndex !== null) {
                        dropWithinCategory(category, dragIndex, index);
                      }
                    }}
                    onDragEnd={() => {
                      setDragCategory(null);
                      setDragIndex(null);
                      setOverIndex(null);
                    }}
                    aria-label={
                      active
                        ? `Edit conversation "${titleOf(s)}"`
                        : `Switch to conversation "${titleOf(s)}"`
                    }
                    title="Drag to reorder within this group"
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
          </div>
        );
      })}
    </div>
  );
}
