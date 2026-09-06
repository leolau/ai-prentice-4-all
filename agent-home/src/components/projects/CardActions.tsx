"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { friendlyError } from "@/components/projects/errors";

import { BusyRegion } from "@/components/ui/BusyRegion";

/**
 * Operator recovery for a card (§12): **Stop** terminates a stuck/running
 * worker and parks the card in blocked (no re-run); **Re-run** reclaims a
 * running card — kill, release the claim, reset to ready — and the
 * dispatcher respawns a fresh worker on its next tick. A blocked card holds
 * no claim (Stop released it), so its way back is the column move to
 * ready (**Make ready**), not a reclaim the backend would refuse.
 */
export function CardActions({
  slug,
  taskId,
  status,
}: {
  slug: string;
  taskId: string;
  status: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"stop" | "reclaim" | "ready" | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (status !== "running" && status !== "ready" && status !== "blocked") {
    return null;
  }

  const act = async (action: "stop" | "reclaim" | "ready") => {
    setBusy(action);
    setError(null);
    try {
      const base = `/api/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(taskId)}`;
      const res =
        action === "ready"
          ? await fetch(base, {
              method: "PATCH",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ status: "ready" }),
            })
          : await fetch(`${base}/${action}`, { method: "POST" });
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) {
        setError(friendlyError({ status: res.status, detail: data.detail }, "That didn't go through."));
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div data-component="CardActions" className="mt-3 flex flex-wrap items-center gap-2">
      {status !== "blocked" ? (
        <BusyRegion busy={busy === "stop"} label="Stopping…">
          <button
            type="button"
            onClick={() => void act("stop")}
            disabled={busy !== null}
            className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium disabled:opacity-40"
          >
            Stop
          </button>
        </BusyRegion>
      ) : null}
      {status === "running" ? (
        <BusyRegion busy={busy === "reclaim"} label="Re-queueing…">
          <button
            type="button"
            onClick={() => void act("reclaim")}
            disabled={busy !== null}
            className="rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] disabled:opacity-40"
          >
            Re-run
          </button>
        </BusyRegion>
      ) : null}
      {status === "blocked" ? (
        <BusyRegion busy={busy === "ready"} label="Making ready…">
          <button
            type="button"
            onClick={() => void act("ready")}
            disabled={busy !== null}
            className="rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] disabled:opacity-40"
          >
            Make ready
          </button>
        </BusyRegion>
      ) : null}
      {error ? (
        <p className="text-xs text-red-300" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
