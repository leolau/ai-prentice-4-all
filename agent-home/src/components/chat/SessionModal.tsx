"use client";

import { useState } from "react";

import type { SessionSummary } from "@/types";

function absolute(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

function relative(ts: number | null): string {
  if (!ts) return "—";
  const secs = Math.max(0, Date.now() / 1000 - ts);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function statusOf(s: SessionSummary): string {
  if (s.ended_at) return "Ended";
  return s.is_active ? "Active" : "Idle";
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-[var(--color-border)] py-2 last:border-b-0">
      <span className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        {label}
      </span>
      <span className="min-w-0 truncate text-right text-sm text-[var(--color-fg)]">
        {value}
      </span>
    </div>
  );
}

/**
 * The conversation details popup, opened by tapping the active session chip.
 * Lets the user edit the conversation name (persisted via the BFF rename route)
 * and shows read-only statistics for the current session.
 */
export function SessionModal({
  session,
  onClose,
  onRename,
}: {
  session: SessionSummary;
  onClose: () => void;
  onRename: (title: string) => Promise<void>;
}) {
  const [name, setName] = useState(session.title ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      await onRename(name.trim());
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rename failed.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      data-component="SessionModal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg)] p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Conversation</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-sm text-[var(--color-muted)]"
          >
            Close
          </button>
        </div>

        <label className="mb-1 block text-xs uppercase tracking-wide text-[var(--color-muted)]">
          Name
        </label>
        <input
          type="text"
          value={name}
          maxLength={200}
          autoFocus
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void save();
            }
          }}
          placeholder="Untitled conversation"
          className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-fg)]"
        />

        {error ? (
          <p className="mt-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        ) : null}

        <div className="mt-4">
          <h3 className="mb-1 text-xs uppercase tracking-wide text-[var(--color-muted)]">
            Statistics
          </h3>
          <Stat label="Messages" value={String(session.message_count)} />
          <Stat label="Source" value={session.source} />
          <Stat label="Status" value={statusOf(session)} />
          <Stat label="Started" value={absolute(session.started_at)} />
          <Stat label="Last active" value={relative(session.last_active)} />
          <Stat label="Session id" value={session.id} />
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-[var(--color-accent-fg)] disabled:opacity-60"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
