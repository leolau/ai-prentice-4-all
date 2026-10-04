"use client";

import { useState } from "react";

import { ActionButton } from "@/components/projects/ActionButton";
import { ActionError } from "@/components/projects/ActionError";
import { useProjectAction } from "@/components/projects/useProjectAction";
import { BusyRegion } from "@/components/ui/BusyRegion";

/**
 * Operator recovery for a card (§12): **Stop** terminates a stuck/running
 * worker and parks the card in blocked (no re-run); **Re-run** reclaims a
 * running card — kill, release the claim, reset to ready — and the
 * dispatcher respawns a fresh worker on its next tick. A blocked card holds
 * no claim (Stop released it), so its way back is the column move to
 * ready (**Make ready**), not a reclaim the backend would refuse.
 */
type Action = "stop" | "reclaim" | "ready";

export function CardActions({
  slug,
  taskId,
  status,
}: {
  slug: string;
  taskId: string;
  status: string;
}) {
  // One lock for the card: busy from the click until the refreshed page
  // has rendered.
  const action = useProjectAction();
  const [last, setLast] = useState<Action | null>(null);
  const inFlight = action.busy;
  const busy = inFlight ? last : null;

  if (status !== "running" && status !== "ready" && status !== "blocked") {
    return null;
  }

  const act = (next: Action) => {
    if (inFlight) return;
    setLast(next);
    const base = `/api/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(taskId)}`;
    void (next === "ready"
      ? action.run(base, { method: "PATCH", body: { status: "ready" } })
      : action.run(`${base}/${next}`));
  };

  return (
    <div data-component="CardActions" className="mt-3 flex flex-wrap items-center gap-2">
      {status !== "blocked" ? (
        <BusyRegion busy={busy === "stop"} label="Stopping…">
          <ActionButton
            busy={busy === "stop"}
            pendingLabel="Stopping…"
            onClick={() => act("stop")}
            disabled={inFlight}
            className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium disabled:opacity-40"
          >
            Stop
          </ActionButton>
        </BusyRegion>
      ) : null}
      {status === "running" ? (
        <BusyRegion busy={busy === "reclaim"} label="Re-queueing…">
          <ActionButton
            busy={busy === "reclaim"}
            pendingLabel="Re-queueing…"
            onClick={() => act("reclaim")}
            disabled={inFlight}
            className="rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] disabled:opacity-40"
          >
            Re-run
          </ActionButton>
        </BusyRegion>
      ) : null}
      {status === "blocked" ? (
        <BusyRegion busy={busy === "ready"} label="Making ready…">
          <ActionButton
            busy={busy === "ready"}
            pendingLabel="Making ready…"
            onClick={() => act("ready")}
            disabled={inFlight}
            className="rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] disabled:opacity-40"
          >
            Make ready
          </ActionButton>
        </BusyRegion>
      ) : null}
      <ActionError action={action} className="flex w-full flex-wrap items-center gap-2 text-xs text-red-300" />
    </div>
  );
}
