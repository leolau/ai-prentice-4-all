"use client";

import { memo, useId, useMemo, useState, type ReactNode } from "react";

import {
  categorizeSession,
  CHAT_CATEGORY_LABELS,
  CHAT_CATEGORY_ORDER,
  type ChatCategory,
} from "@/lib/chat/categorize";
import { NEW_KEY } from "@/lib/chat/chat-controller";
import type { LastReadMap } from "@/lib/chat/last-read";
import type { SessionSummary } from "@/types";

export type CategoryFilter = "all" | ChatCategory;

export interface ConversationListProps {
  sessions: SessionSummary[];
  /** The open conversation (null = the unsaved "New conversation"). */
  selectedId: string | null;
  /** Turn keys with a live turn (`useBusyKeys`). */
  busyKeys: readonly string[];
  /** Last-read markers; null until read on the client (no unread dots then). */
  lastRead: LastReadMap | null;
  onSelect(id: string): void;
  /** Return to the unsaved "New conversation" (shown while it is open or busy). */
  onSelectNew(): void;
  /** Controls above the search box (e.g. the profile picker). */
  header?: ReactNode;
  /** Collapsible filter controls (tag filters); no toggle when absent. */
  filters?: ReactNode;
  /** Some filter is active — marks the toggle. */
  filtersActive?: boolean;
  /** Full-text search over message bodies for the current query. */
  onSearchMessages?(query: string): void;
  /** Rendered atop the rows (the message-search results). */
  searchPanel?: ReactNode;
  /** Clock for categories and times (tests). */
  now?: Date;
  className?: string;
}

export function titleOf(s: Pick<SessionSummary, "title" | "preview">): string {
  return s.title || s.preview || "Untitled";
}

const activityOf = (s: SessionSummary): number => s.last_active ?? s.started_at ?? 0;

/** Newest first; ties keep the server order. */
export function sortNewestFirst(sessions: SessionSummary[]): SessionSummary[] {
  return sessions
    .map((s, i) => ({ s, i }))
    .sort((a, b) => activityOf(b.s) - activityOf(a.s) || a.i - b.i)
    .map((x) => x.s);
}

/** "now", "5m", "3h", "2d", then a short date. */
export function relativeTime(epochSeconds: number | null, now: Date): string {
  if (epochSeconds == null || !Number.isFinite(epochSeconds)) return "";
  const secs = Math.max(0, now.getTime() / 1000 - epochSeconds);
  if (secs < 60) return "now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  if (secs < 7 * 86400) return `${Math.floor(secs / 86400)}d`;
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}

export function isUnread(s: SessionSummary, lastRead: LastReadMap | null): boolean {
  if (!lastRead || s.archived || s.last_active == null) return false;
  const read = lastRead[s.id];
  return read == null || s.last_active > read;
}

const LiveDot = () => (
  <span
    aria-hidden="true"
    data-component="LiveDot"
    className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-[var(--color-accent)]"
  />
);

const rowClass = (selected: boolean) =>
  `flex w-full items-start gap-3 border-l-[3px] px-3 py-2.5 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-accent)] ${
    selected
      ? "border-l-[var(--color-accent)] bg-[var(--color-surface-2)]"
      : "border-l-transparent hover:bg-[var(--color-surface-2)]/60"
  }`;

const ConversationRow = memo(function ConversationRow({
  session,
  selected,
  busy,
  unread,
  time,
  onSelect,
}: {
  session: SessionSummary;
  selected: boolean;
  busy: boolean;
  unread: boolean;
  time: string;
  onSelect(id: string): void;
}) {
  const title = titleOf(session);
  const preview = session.title ? session.preview : null;
  return (
    <li>
      <button
        type="button"
        data-component="ConversationRow"
        data-session-id={session.id}
        aria-current={selected ? "true" : undefined}
        onClick={() => onSelect(session.id)}
        className={rowClass(selected)}
      >
        <span className="min-w-0 flex-1">
          <span
            className={`block truncate text-sm ${
              unread ? "font-semibold text-[var(--color-fg)]" : "font-medium text-[var(--color-fg)]"
            }`}
          >
            {title}
          </span>
          {preview ? (
            <span className="block truncate text-xs text-[var(--color-muted)]">{preview}</span>
          ) : null}
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1 text-[11px] text-[var(--color-muted)]">
          <span suppressHydrationWarning>{time}</span>
          {busy ? (
            <>
              <LiveDot />
              <span className="sr-only">Agent working</span>
            </>
          ) : unread ? (
            <>
              <span
                aria-hidden="true"
                data-component="UnreadDot"
                className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-accent)]"
              />
              <span className="sr-only">Unread</span>
            </>
          ) : null}
        </span>
      </button>
    </li>
  );
});

/**
 * The Chats sidebar (desktop) / list screen (phone): a search box over
 * titles and previews, category chips (All by default), and the
 * conversations newest first with preview, time, unread and live dots.
 */
export function ConversationList({
  sessions,
  selectedId,
  busyKeys,
  lastRead,
  onSelect,
  onSelectNew,
  header,
  filters,
  filtersActive = false,
  onSearchMessages,
  searchPanel,
  now: nowProp,
  className = "",
}: ConversationListProps) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<CategoryFilter>("all");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filtersId = useId();

  // One clock per session list so categories and times agree.
  const now = useMemo(
    () => nowProp ?? new Date(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nowProp, sessions],
  );
  const sorted = useMemo(
    () =>
      sortNewestFirst(sessions).map((s) => ({ s, cat: categorizeSession(s, now) })),
    [sessions, now],
  );
  const counts = useMemo(() => {
    const c: Record<ChatCategory, number> = { kanban: 0, daily: 0, scheduled: 0, others: 0 };
    for (const { cat } of sorted) c[cat] += 1;
    return c;
  }, [sorted]);
  const available = CHAT_CATEGORY_ORDER.filter((c) => counts[c] > 0);
  // A category that emptied out (last row archived) falls back to All.
  const effective: CategoryFilter =
    category === "all" || counts[category] > 0 ? category : "all";

  const q = query.trim().toLowerCase();
  const items = useMemo(
    () =>
      sorted
        .filter(({ cat }) => effective === "all" || cat === effective)
        .filter(
          ({ s }) =>
            !q ||
            (s.title ?? "").toLowerCase().includes(q) ||
            (s.preview ?? "").toLowerCase().includes(q),
        )
        .map(({ s }) => s),
    [sorted, effective, q],
  );

  const newBusy = busyKeys.includes(NEW_KEY);
  const showNew = selectedId === null || newBusy;

  const chip = (value: CategoryFilter, label: string, count: number) => (
    <button
      key={value}
      type="button"
      aria-pressed={effective === value}
      onClick={() => setCategory(value)}
      className={`shrink-0 rounded-full border px-2.5 py-0.5 text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] ${
        effective === value
          ? "border-[var(--color-accent)] bg-[var(--color-surface-2)] text-[var(--color-fg)]"
          : "border-[var(--color-border)] text-[var(--color-muted)]"
      }`}
    >
      {label} <span className="opacity-70">{count}</span>
    </button>
  );

  return (
    <nav
      data-component="ConversationList"
      aria-label="Conversations"
      className={`flex min-h-0 flex-1 flex-col ${className}`}
    >
      <div className="shrink-0 space-y-2 border-b border-[var(--color-border)] p-3">
        {header}
        <div className="flex items-center gap-2">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && query) {
                e.preventDefault();
                setQuery("");
              }
            }}
            aria-label="Search conversations"
            placeholder="Search conversations…"
            className="min-w-0 flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-fg)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
          />
          {filters ? (
            <button
              type="button"
              aria-expanded={filtersOpen}
              aria-controls={filtersId}
              onClick={() => setFiltersOpen((v) => !v)}
              className={`relative shrink-0 rounded-lg border px-2.5 py-1.5 text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] ${
                filtersOpen || filtersActive
                  ? "border-[var(--color-accent)] text-[var(--color-fg)]"
                  : "border-[var(--color-border)] text-[var(--color-muted)]"
              }`}
            >
              Filters
              {filtersActive ? (
                <span className="sr-only"> (active)</span>
              ) : null}
            </button>
          ) : null}
        </div>
        {filters && filtersOpen ? (
          <div id={filtersId} data-component="ConversationFilters">
            {filters}
          </div>
        ) : null}
        {available.length > 0 ? (
          <div
            role="group"
            aria-label="Category"
            className="flex gap-1 overflow-x-auto [scrollbar-width:none]"
          >
            {chip("all", "All", sessions.length)}
            {available.map((c) => chip(c, CHAT_CATEGORY_LABELS[c], counts[c]))}
          </div>
        ) : null}
      </div>

      {/* The one scroll container of the list screen (message-search
       * results scroll with the rows, not in a box of their own). */}
      <ul className="min-h-0 flex-1 overflow-y-auto overscroll-contain pb-2">
        {searchPanel ? <li data-component="ConversationSearchPanel">{searchPanel}</li> : null}
        {showNew ? (
          <li>
            <button
              type="button"
              data-component="ConversationRow"
              aria-current={selectedId === null ? "true" : undefined}
              onClick={onSelectNew}
              className={rowClass(selectedId === null)}
            >
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--color-fg)]">
                New conversation
              </span>
              {newBusy ? (
                <span className="flex items-center gap-1 text-[11px] text-[var(--color-muted)]">
                  <LiveDot />
                  <span className="sr-only">Agent working</span>
                </span>
              ) : null}
            </button>
          </li>
        ) : null}
        {items.map((s) => (
          <ConversationRow
            key={s.id}
            session={s}
            selected={s.id === selectedId}
            busy={busyKeys.includes(s.id)}
            unread={s.id !== selectedId && isUnread(s, lastRead)}
            time={relativeTime(s.last_active ?? s.started_at, now)}
            onSelect={onSelect}
          />
        ))}
        {items.length === 0 ? (
          <li className="px-3 py-6 text-center text-sm text-[var(--color-muted)]">
            {q ? "No conversations match." : "No conversations yet."}
          </li>
        ) : null}
        {q && onSearchMessages ? (
          <li className="px-3 py-2">
            <button
              type="button"
              onClick={() => onSearchMessages(query.trim())}
              className="w-full rounded-lg border border-dashed border-[var(--color-border)] px-3 py-2 text-left text-xs text-[var(--color-muted)] hover:text-[var(--color-fg)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
            >
              Search message text for “{query.trim()}”
            </button>
          </li>
        ) : null}
      </ul>
    </nav>
  );
}
