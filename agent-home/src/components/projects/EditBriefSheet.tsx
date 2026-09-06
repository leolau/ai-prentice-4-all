"use client";

import { useState } from "react";

import { BusyRegion } from "@/components/ui/BusyRegion";
import type { ProjectDetail, ProjectStatus } from "@/types";

/** The subset of `PATCH /{slug}` a lead edits from the brief. */
interface BriefFields {
  name: string;
  goal: string;
  description: string;
  target_audience: string;
}

const PAUSABLE: Partial<Record<ProjectStatus, { to: ProjectStatus; label: string }>> = {
  active: { to: "paused", label: "Pause the project" },
  paused: { to: "active", label: "Resume the project" },
};

/** Only the fields that changed — the API treats every key as an assignment. */
export function briefPatch(
  before: BriefFields,
  after: BriefFields,
): Partial<BriefFields> {
  const patch: Partial<BriefFields> = {};
  for (const key of Object.keys(after) as (keyof BriefFields)[]) {
    if (after[key].trim() !== before[key].trim()) patch[key] = after[key].trim();
  }
  return patch;
}

/**
 * Edit the brief in place — name, goal, requirements, audience — plus the
 * pause/resume toggle, the one lifecycle move that isn't destructive enough
 * for the lifecycle menu. Bottom sheet like {@link AddToProjectSheet}.
 */
export function EditBriefSheet({
  project,
  onClose,
}: {
  project: ProjectDetail;
  /** Called after a successful save too — the caller refreshes. */
  onClose: () => void;
}) {
  const before: BriefFields = {
    name: project.name,
    goal: project.goal ?? "",
    description: project.description ?? "",
    target_audience: project.target_audience ?? "",
  };
  const [fields, setFields] = useState<BriefFields>(before);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<BriefFields>>({});

  const slugPath = `/api/projects/${encodeURIComponent(project.slug)}`;

  const patch = async (body: Record<string, string>) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(slugPath, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) {
        setError(data.detail ?? "The change was not saved.");
        return false;
      }
      return true;
    } catch {
      setError("Could not reach the server.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    const next: Partial<BriefFields> = {};
    if (!fields.goal.trim()) next.goal = "A project needs a goal sentence.";
    if (!fields.name.trim()) next.name = "A project needs a name.";
    setFieldErrors(next);
    if (Object.keys(next).length > 0) return;
    const body = briefPatch(before, fields);
    if (Object.keys(body).length === 0) {
      onClose();
      return;
    }
    if (await patch(body)) onClose();
  };

  const toggle = PAUSABLE[project.status];

  const set = (key: keyof BriefFields) => (value: string) =>
    setFields((prev) => ({ ...prev, [key]: value }));

  const inputClass = (bad: boolean) =>
    `w-full rounded-xl border px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)] ${
      bad ? "border-red-400" : "border-[var(--color-border)]"
    } bg-[var(--color-surface)]`;

  return (
    <div
      data-component="EditBriefSheet"
      className="fixed inset-0 z-50 flex items-end bg-black/50"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label="Edit brief"
        className="mx-auto flex max-h-[90vh] w-full max-w-md flex-col gap-3 overflow-y-auto rounded-t-2xl border-x border-t border-[var(--color-border)] bg-[var(--color-surface)] p-4"
        style={{ paddingBottom: "calc(var(--safe-bottom) + 1rem)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Edit brief</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-sm text-[var(--color-muted)]"
          >
            Close
          </button>
        </div>

        <BusyRegion busy={busy} label="Saving…">
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <label className="flex flex-col gap-1 text-sm">
              <span>Name</span>
              <input
                value={fields.name}
                onChange={(e) => set("name")(e.target.value)}
                className={inputClass(Boolean(fieldErrors.name))}
              />
              {fieldErrors.name ? (
                <span className="text-xs text-red-400">{fieldErrors.name}</span>
              ) : null}
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span>Goal — one sentence</span>
              <input
                value={fields.goal}
                onChange={(e) => set("goal")(e.target.value)}
                className={inputClass(Boolean(fieldErrors.goal))}
              />
              {fieldErrors.goal ? (
                <span className="text-xs text-red-400">{fieldErrors.goal}</span>
              ) : null}
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span>Requirements — what “done” looks like</span>
              <textarea
                value={fields.description}
                onChange={(e) => set("description")(e.target.value)}
                rows={5}
                className={inputClass(false)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span>Audience (optional)</span>
              <input
                value={fields.target_audience}
                onChange={(e) => set("target_audience")(e.target.value)}
                placeholder="the founders, my team, me"
                className={inputClass(false)}
              />
            </label>

            {error ? (
              <p role="alert" className="text-sm text-red-400">
                {error}
              </p>
            ) : null}

            <div className="flex items-center gap-2">
              {toggle ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={async () => {
                    if (await patch({ status: toggle.to })) onClose();
                  }}
                  className="rounded-xl border border-[var(--color-border)] px-3 py-2 text-sm disabled:opacity-50"
                >
                  {toggle.label}
                </button>
              ) : null}
              <span className="flex-1" />
              <button
                type="submit"
                disabled={busy}
                className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </form>
        </BusyRegion>
      </div>
    </div>
  );
}
