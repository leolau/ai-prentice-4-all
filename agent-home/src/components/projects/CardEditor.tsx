"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { cardMoves, cardPatch } from "@/components/projects/cardMoves";
import { BusyRegion } from "@/components/ui/BusyRegion";
import type { ProjectCardDetail } from "@/types";

/**
 * The hand edits a member may make to a card (§10): title, brief, which
 * project profile works it, and the column it sits in. Every change is one
 * `PATCH /api/projects/{slug}/cards/{id}`; the backend refuses moves the
 * dispatcher owns and names why.
 */
export function CardEditor({
  slug,
  card,
  profiles,
}: {
  slug: string;
  card: ProjectCardDetail;
  /** The project's profiles — the only valid assignees. */
  profiles: string[];
}) {
  const router = useRouter();
  const before = {
    title: card.title,
    body: card.body ?? "",
    assignee: card.assignee ?? "",
  };
  const [editing, setEditing] = useState(false);
  const [fields, setFields] = useState(before);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const patch = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(card.id)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) {
        setError(data.detail ?? "The change was not saved.");
        return false;
      }
      router.refresh();
      return true;
    } catch {
      setError("Could not reach the server.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!fields.title.trim()) {
      setError("A card needs a title.");
      return;
    }
    const body = cardPatch(before, fields);
    if (Object.keys(body).length === 0) {
      setEditing(false);
      return;
    }
    if (await patch(body)) setEditing(false);
  };

  const moves = cardMoves(card.status);
  const assignees = Array.from(
    new Set([...(card.assignee ? [card.assignee] : []), ...profiles]),
  );

  const inputClass =
    "w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]";
  const buttonClass =
    "rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium disabled:opacity-40";

  return (
    <div data-component="CardEditor" className="mt-3 flex flex-col gap-2">
      <BusyRegion busy={busy} label="Saving…">
        {editing ? (
          <form
            className="flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-xs text-[var(--color-muted)]">Title</span>
              <input
                value={fields.title}
                onChange={(e) =>
                  setFields((prev) => ({ ...prev, title: e.target.value }))
                }
                className={inputClass}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-xs text-[var(--color-muted)]">Brief</span>
              <textarea
                value={fields.body}
                onChange={(e) =>
                  setFields((prev) => ({ ...prev, body: e.target.value }))
                }
                rows={5}
                className={inputClass}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-xs text-[var(--color-muted)]">
                Worked by
              </span>
              <select
                value={fields.assignee}
                onChange={(e) =>
                  setFields((prev) => ({ ...prev, assignee: e.target.value }))
                }
                disabled={card.status === "running"}
                className={inputClass}
              >
                <option value="">unassigned</option>
                {assignees.map((profile) => (
                  <option key={profile} value={profile}>
                    {profile}
                  </option>
                ))}
              </select>
              {card.status === "running" ? (
                <span className="text-xs text-[var(--color-muted)]">
                  Stop or wait for the worker before reassigning.
                </span>
              ) : null}
            </label>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setFields(before);
                  setError(null);
                  setEditing(false);
                }}
                className={buttonClass}
              >
                Cancel
              </button>
              <span className="flex-1" />
              <button
                type="submit"
                disabled={busy}
                className="rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent-fg)] disabled:opacity-40"
              >
                Save
              </button>
            </div>
          </form>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setEditing(true)}
              disabled={busy}
              className={buttonClass}
            >
              Edit card
            </button>
            {moves.length > 0 ? (
              <label className="flex items-center gap-1 text-xs text-[var(--color-muted)]">
                <span>Move</span>
                <select
                  aria-label="Move card"
                  value=""
                  disabled={busy}
                  onChange={(e) => {
                    const to = e.target.value;
                    if (!to) return;
                    void patch({ status: to });
                  }}
                  className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1.5 text-xs"
                >
                  <option value="">to…</option>
                  {moves.map((move) => (
                    <option key={move.to} value={move.to}>
                      {move.label}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
        )}
      </BusyRegion>
      {error ? (
        <p className="text-xs text-red-300" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
