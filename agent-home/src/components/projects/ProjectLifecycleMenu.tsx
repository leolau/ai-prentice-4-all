"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { BusyRegion } from "@/components/ui/BusyRegion";
import type { ProjectDetail } from "@/types";

/**
 * The `[⋯]` overflow menu in the detail header (§13) — never bare buttons
 * in the header: archive is the ordinary verb, restore only exists on the
 * shelf, and delete is the narrow exception (decision 17).
 *
 * Who sees what: a viewer sees none of the three; a member who is not a
 * lead sees Archive disabled with the reason — "why can't I" is a better
 * question than "where did it go".
 */
export function ProjectLifecycleMenu({
  project,
  callerUserId,
  isInstanceAdmin,
}: {
  project: ProjectDetail;
  callerUserId: string;
  isInstanceAdmin: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<null | "archive" | "delete">(null);
  const [reason, setReason] = useState("");
  const [typedSlug, setTypedSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const callerRole =
    project.members.find((member) => member.user_id === callerUserId)?.role ??
    null;
  const canLead =
    isInstanceAdmin ||
    project.owner_user_id === callerUserId ||
    callerRole === "lead";
  // A viewer — or a shared-read principal with no member row — sees none of
  // the three.
  if (!canLead && callerRole !== "member") return null;

  const slugPath = `/api/projects/${encodeURIComponent(project.slug)}`;
  const hasDoneWork = project.outputs.some(
    (output) => output.status === "delivered" || output.status === "accepted",
  );
  // The §12 preconditions, derived from the same record the page renders:
  // archived, and no run, no delivered/accepted output, no card. The server
  // re-checks all of them — this gate only decides what the menu offers.
  // The card count is the archived-inclusive one the delete route uses (U5).
  const cardCount =
    project.card_rollup.total_with_archived ?? project.card_rollup.total;
  const deleteEligible =
    project.archived &&
    project.runs.length === 0 &&
    !hasDoneWork &&
    cardCount === 0;

  const closeAll = () => {
    setOpen(false);
    setDialog(null);
    setReason("");
    setTypedSlug("");
    setError(null);
  };

  const detailMessage = (data: {
    detail?: unknown;
    error?: string;
  }): string =>
    typeof data.detail === "string" && data.detail
      ? data.detail
      : typeof data.error === "string" && data.error
        ? data.error
        : "That didn't go through.";

  const archive = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${slugPath}/archive`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(reason.trim() ? { reason: reason.trim() } : {}),
      });
      const data = (await res.json().catch(() => ({}))) as {
        detail?: unknown;
        error?: string;
      };
      if (!res.ok) {
        setError(detailMessage(data));
        return;
      }
      // The write answers with the updated row; the server read merges it.
      closeAll();
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${slugPath}/restore`, { method: "POST" });
      const data = (await res.json().catch(() => ({}))) as {
        detail?: unknown;
        error?: string;
      };
      if (!res.ok) {
        setError(detailMessage(data));
        return;
      }
      closeAll();
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const deletePermanently = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `${slugPath}?confirm=${encodeURIComponent(typedSlug)}`,
        { method: "DELETE" },
      );
      const data = (await res.json().catch(() => ({}))) as {
        detail?: unknown;
        error?: string;
      };
      if (!res.ok) {
        setError(detailMessage(data));
        return;
      }
      // The row is gone — leave before the detail page renders a ghost.
      router.push("/projects");
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const itemClass =
    "block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-[var(--color-surface-2)] disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <BusyRegion busy={busy} label="Working…">
      <div data-component="ProjectLifecycleMenu" className="relative">
        <button
          type="button"
          aria-label="Project actions"
          aria-expanded={open}
          onClick={() => setOpen((prev) => !prev)}
          className="rounded-lg border border-[var(--color-border)] px-2.5 py-1 text-sm text-[var(--color-muted)]"
        >
          ⋯
        </button>

        {/* Kept mounted (hidden) so the affordances are inspectable even
            closed; a fixed backdrop catches taps outside. */}
        {open ? (
          <button
            type="button"
            aria-label="Close menu"
            tabIndex={-1}
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-30 cursor-default"
          />
        ) : null}
        <div
          data-component="ProjectLifecycleMenuItems"
          className={`absolute right-0 z-40 mt-1 w-60 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-1 shadow-lg ${open ? "" : "hidden"}`}
        >
          {project.archived ? (
            <button
              type="button"
              onClick={() => void restore()}
              disabled={busy || !canLead}
              title={canLead ? undefined : "Only a lead can restore this project"}
              className={itemClass}
            >
              Restore project
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setDialog("archive")}
              disabled={busy || !canLead}
              title={canLead ? undefined : "Only a lead can archive this project"}
              className={itemClass}
            >
              Archive project…
            </button>
          )}
          {canLead && deleteEligible ? (
            <button
              type="button"
              onClick={() => setDialog("delete")}
              disabled={busy}
              className={`${itemClass} text-red-400`}
            >
              Delete permanently…
            </button>
          ) : null}
          {!canLead ? (
            <p className="px-3 py-2 text-xs text-[var(--color-muted)]">
              Only a lead can change this project&apos;s lifecycle.
            </p>
          ) : null}
        </div>

        {dialog === "archive" ? (
          <div
            data-component="ArchiveDialog"
            role="dialog"
            aria-modal="true"
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          >
            <div className="w-full max-w-sm rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
              <h2 className="text-base font-semibold">Archive this project?</h2>
              <p className="mt-1 text-sm text-[var(--color-muted)]">
                It leaves the list and stops running — every run, output and
                score stays on the record, and you can restore it any time.
              </p>
              <label className="mt-3 flex flex-col gap-1 text-sm">
                <span>Why? (optional, recorded)</span>
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Done for the term"
                  className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
                />
              </label>
              {error ? (
                <p role="alert" className="mt-2 text-sm text-red-400">
                  {error}
                </p>
              ) : null}
              <div className="mt-3 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={closeAll}
                  className="rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void archive()}
                  disabled={busy}
                  className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                >
                  Archive
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {dialog === "delete" ? (
          <div
            data-component="DeleteDialog"
            role="dialog"
            aria-modal="true"
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          >
            <div className="w-full max-w-sm rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
              <h2 className="text-base font-semibold text-red-400">
                Delete permanently?
              </h2>
              <p className="mt-1 text-sm text-[var(--color-muted)]">
                This erases the record — goal, outputs, links and settings.
                There is no undo. Archive instead keeps everything and is one
                restore away.
              </p>
              <label className="mt-3 flex flex-col gap-1 text-sm">
                <span>
                  Type <code className="font-mono">{project.slug}</code> to
                  confirm
                </span>
                <input
                  value={typedSlug}
                  onChange={(e) => setTypedSlug(e.target.value)}
                  autoComplete="off"
                  className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 font-mono text-sm outline-none focus:border-[var(--color-accent)]"
                />
              </label>
              {error ? (
                <p role="alert" className="mt-2 text-sm text-red-400">
                  {error}
                </p>
              ) : null}
              <div className="mt-3 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={closeAll}
                  className="rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void deletePermanently()}
                  disabled={busy || typedSlug !== project.slug}
                  className="rounded-xl bg-red-500 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  Delete forever
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </BusyRegion>
  );
}
