"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { BusyRegion } from "@/components/ui/BusyRegion";

/**
 * Operator recovery for a card (§12): **Stop** terminates a stuck/running
 * worker and parks the card in blocked (no re-run); **Re-run** (or Retry
 * when blocked) reclaims it — kill, release the claim, reset to ready —
 * and the dispatcher respawns a fresh worker on its next tick.
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
  const [busy, setBusy] = useState<"stop" | "reclaim" | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (status !== "running" && status !== "ready" && status !== "blocked") {
    return null;
  }

  const act = async (action: "stop" | "reclaim") => {
    setBusy(action);
    setError(null);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(taskId)}/${action}`,
        { method: "POST" },
      );
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) {
        setError(data.detail ?? "That didn't go through.");
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
      <BusyRegion busy={busy === "reclaim"} label="Re-queueing…">
        <button
          type="button"
          onClick={() => void act("reclaim")}
          disabled={busy !== null}
          className="rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] disabled:opacity-40"
        >
          {status === "blocked" ? "Retry" : "Re-run"}
        </button>
      </BusyRegion>
      {error ? (
        <p className="text-xs text-red-300" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
