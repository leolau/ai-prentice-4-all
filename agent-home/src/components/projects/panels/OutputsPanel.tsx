"use client";

import { useState } from "react";

import { dateTimeLabel } from "@/components/projects/format";
import { BusyRegion } from "@/components/ui/BusyRegion";
import { Pill } from "@/components/ui/Pill";
import type { ProjectOutputStatus, ProjectOutputWithDeliveries } from "@/types";

const STATUS_TONE: Record<
  ProjectOutputStatus,
  "muted" | "accent" | "success" | "warning" | "danger"
> = {
  pending: "muted",
  in_progress: "accent",
  delivered: "warning",
  accepted: "success",
  dropped: "danger",
};

/**
 * The deliverables (§6.1). Undelivered required ones lead, because they are
 * what stands between the project and done. **Accept lives here and nowhere
 * else** — accepting is the judgement that an output met its spec, and one
 * place for a judgement keeps it honest.
 */
export function OutputsPanel({
  slug,
  outputs: initial,
}: {
  slug: string;
  outputs: ProjectOutputWithDeliveries[];
}) {
  const [outputs, setOutputs] = useState(initial);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const accept = async (outputId: string) => {
    setBusyId(outputId);
    setError(null);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/outputs/${encodeURIComponent(outputId)}/accept`,
        { method: "POST" },
      );
      if (!res.ok) throw new Error("accept");
      // The accept route returns the bare row; keep the joined deliveries.
      const updated = (await res.json()) as Record<string, unknown>;
      setOutputs((prev) =>
        prev.map((row) =>
          row.id === outputId ? { ...row, ...updated } : row,
        ),
      );
    } catch {
      setError("That didn't stick — try again.");
    } finally {
      setBusyId(null);
    }
  };

  const rank = (output: ProjectOutputWithDeliveries) =>
    // Undelivered required outputs first, then required, then the rest.
    output.required && output.status !== "accepted" && output.status !== "delivered"
      ? 0
      : output.required
        ? 1
        : 2;
  const sorted = [...outputs].sort(
    (a, b) => rank(a) - rank(b) || a.seq - b.seq,
  );

  return (
    <section
      id="panel-outputs"
      data-component="OutputsPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Outputs
      </h2>

      {error ? (
        <p className="mt-2 text-sm text-red-300">{error}</p>
      ) : null}

      {sorted.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Add an output — declaring the deliverable is what makes a run
          accountable to something.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-3">
          {sorted.map((output) => (
            <li
              key={output.id}
              data-component="OutputRow"
              className="rounded-lg bg-[var(--color-surface-2)] p-3"
            >
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-sm font-medium">
                  {output.title}
                </span>
                <span className="text-xs text-[var(--color-muted)]">
                  {output.required ? "required" : "optional"}
                </span>
                <Pill tone={STATUS_TONE[output.status]}>
                  {output.status.replace("_", " ")}
                </Pill>
              </div>
              {output.spec ? (
                <p className="mt-1 text-xs text-[var(--color-muted)]">
                  {output.spec}
                </p>
              ) : null}
              {output.deliveries.length > 0 ? (
                <ul className="mt-2 flex flex-col gap-1 text-xs text-[var(--color-muted)]">
                  {output.deliveries.map((delivery) => (
                    <li key={delivery.id}>
                      delivered{" "}
                      {delivery.run_id ? `on a run` : "by hand"}
                      {delivery.label ? ` → ${delivery.label}` : ""} ·{" "}
                      {dateTimeLabel(delivery.delivered_at)}
                    </li>
                  ))}
                </ul>
              ) : null}
              {output.status === "delivered" ? (
                <BusyRegion busy={busyId === output.id} label="Accepting…">
                  <button
                    type="button"
                    onClick={() => void accept(output.id)}
                    className="mt-2 rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)]"
                  >
                    Accept
                  </button>
                </BusyRegion>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
