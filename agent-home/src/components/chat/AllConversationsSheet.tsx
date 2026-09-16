"use client";

import { useEffect, useState } from "react";

import { Spinner } from "@/components/ui/Spinner";
import {
  CHAT_CATEGORY_LABELS,
  CHAT_CATEGORY_ORDER,
  groupSessionsByCategory,
} from "@/lib/chat/categorize";
import { withProfileQuery } from "@/lib/chat/profile";
import type { SessionSummary } from "@/types";

function titleOf(s: SessionSummary): string {
  return s.title || s.preview || "Untitled";
}

/**
 * The full conversation browser, opened from the session strip's "All
 * conversations" button. Unlike the strip (which excludes cron sessions and
 * only fetches a first-paint-sized window — see `CHAT_SESSION_LIST_LIMIT`),
 * this fetches the complete recent set including scheduled/cron sessions, on
 * demand only when opened, and groups it into four collapsible sections:
 * Kanban (card-worker sessions), Daily (today's, everything else),
 * Scheduled (cron-triggered), and Others (older, non-kanban, non-cron).
 */
export function AllConversationsSheet({
  onClose,
  onSelect,
  profile,
}: {
  onClose: () => void;
  onSelect: (id: string) => void;
  profile?: string;
}) {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const params = new URLSearchParams({ order: "recent", limit: "200" });
        const res = await fetch(
          withProfileQuery(`/api/chat/sessions?${params.toString()}`, profile),
          { cache: "no-store" },
        );
        const body = (await res.json()) as {
          sessions?: SessionSummary[];
          detail?: string;
        };
        if (!active) return;
        if (!res.ok) throw new Error(body.detail ?? "Failed to load conversations.");
        setSessions(body.sessions ?? []);
      } catch (err) {
        if (!active) return;
        setError(
          err instanceof Error ? err.message : "Failed to load conversations.",
        );
        setSessions([]);
      }
    })();
    return () => {
      active = false;
    };
  }, [profile]);

  const groups = sessions ? groupSessionsByCategory(sessions) : null;

  return (
    <div
      data-component="AllConversationsSheet"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[80dvh] w-full max-w-sm flex-col rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg)] p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">All conversations</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-sm text-[var(--color-muted)]"
          >
            Close
          </button>
        </div>

        {error ? (
          <p className="mb-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        ) : null}

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
          {groups === null ? (
            <p
              role="status"
              aria-live="polite"
              className="flex items-center justify-center gap-2 py-8 text-center text-sm text-[var(--color-accent)]"
            >
              <Spinner size="md" />
              Loading your conversations…
            </p>
          ) : sessions && sessions.length === 0 ? (
            <p className="py-8 text-center text-sm text-[var(--color-muted)]">
              No conversations yet.
            </p>
          ) : (
            CHAT_CATEGORY_ORDER.map((category) => {
              const items = groups[category];
              if (items.length === 0) return null;
              return (
                <details
                  key={category}
                  open
                  className="rounded-xl border border-[var(--color-border)]"
                >
                  <summary className="cursor-pointer select-none px-3 py-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                    {CHAT_CATEGORY_LABELS[category]}
                    <span className="ml-1.5 font-normal normal-case text-[var(--color-muted)]">
                      ({items.length})
                    </span>
                  </summary>
                  <div className="space-y-2 border-t border-[var(--color-border)] p-2">
                    {items.map((s) => (
                      <button
                        key={s.id}
                        type="button"
                        onClick={() => {
                          onSelect(s.id);
                          onClose();
                        }}
                        className="flex w-full items-center gap-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-left"
                      >
                        <span className="min-w-0 flex-1 truncate text-sm text-[var(--color-fg)]">
                          {titleOf(s)}
                        </span>
                      </button>
                    ))}
                  </div>
                </details>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
