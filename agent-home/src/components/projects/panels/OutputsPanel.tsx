"use client";

import Link from "next/link";
import { useState } from "react";

import { applyAcceptEnvelope } from "@/components/projects/envelopes";
import { dateTimeLabel } from "@/components/projects/format";
import { BusyRegion } from "@/components/ui/BusyRegion";
import { Pill } from "@/components/ui/Pill";
import type {
  ProjectOutputKind,
  ProjectOutputStatus,
  ProjectOutputWithDeliveries,
} from "@/types";

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
  archived = false,
}: {
  slug: string;
  outputs: ProjectOutputWithDeliveries[];
  /** §13: a shelved project offers restore as the only write. */
  archived?: boolean;
}) {
  const [outputs, setOutputs] = useState(initial);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offersClosure, setOffersClosure] = useState(false);

  // Add-output form state
  const [newTitle, setNewTitle] = useState("");
  const [newSpec, setNewSpec] = useState("");
  const [newKind, setNewKind] = useState<ProjectOutputKind>("artifact");
  const [newRequired, setNewRequired] = useState(true);
  const [adding, setAdding] = useState(false);

  const accept = async (outputId: string) => {
    setBusyId(outputId);
    setError(null);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/outputs/${encodeURIComponent(outputId)}/accept`,
        { method: "POST" },
      );
      if (!res.ok) throw new Error("accept");
      // The accept route answers with the updated row + the closure offer;
      // merge the row (the joined deliveries survive the spread) so the
      // Accept button disappears without a reload.
      const payload = (await res.json()) as {
        output?: Partial<ProjectOutputWithDeliveries>;
        offers_closure?: boolean;
      };
      setOutputs((prev) => applyAcceptEnvelope(prev, outputId, payload).outputs);
      if (payload.offers_closure === true) setOffersClosure(true);
    } catch {
      setError("That didn't stick — try again.");
    } finally {
      setBusyId(null);
    }
  };

  const add = async () => {
    const title = newTitle.trim();
    if (!title) return;
    setAdding(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/outputs`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            title,
            spec: newSpec.trim() || undefined,
            kind: newKind,
            required: newRequired,
          }),
        },
      );
      const data = (await res.json().catch(() => ({}))) as ProjectOutputWithDeliveries &
        { detail?: string };
      if (!res.ok) throw new Error(data.detail ?? "Could not add the output.");
      setOutputs((prev) => [...prev, { ...data, deliveries: [] }]);
      setNewTitle("");
      setNewSpec("");
      setNewKind("artifact");
      setNewRequired(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't go through.");
    } finally {
      setAdding(false);
    }
  };

  const remove = async (outputId: string) => {
    setBusyId(`del:${outputId}`);
    setError(null);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/outputs/${encodeURIComponent(outputId)}`,
        { method: "DELETE" },
      );
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) throw new Error(data.detail ?? "Could not remove the output.");
      setOutputs((prev) => prev.filter((o) => o.id !== outputId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't go through.");
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

      {archived ? (
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          This project is archived — restore it (⋯) to accept outputs.
        </p>
      ) : null}

      {offersClosure ? (
        <p
          data-component="ClosureOffer"
          className="mt-2 rounded-lg border border-[var(--color-accent)]/40 bg-[var(--color-surface-2)] px-3 py-2 text-sm"
        >
          Every required output is now accepted — this project offers
          closure. Decide it on{" "}
          <Link href="/projects" className="text-[var(--color-accent)] underline">
            /projects
          </Link>
          .
        </p>
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
              {output.status === "delivered" && !archived ? (
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
              {output.status === "pending" && !archived ? (
                <BusyRegion busy={busyId === `del:${output.id}`} label="Removing…">
                  <button
                    type="button"
                    onClick={() => void remove(output.id)}
                    className="mt-2 text-xs text-[var(--color-muted)] underline disabled:opacity-40"
                  >
                    Remove
                  </button>
                </BusyRegion>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {!archived ? (
        <BusyRegion busy={adding} label="Adding output…" className="mt-3">
          <div data-component="AddOutputForm" className="flex flex-col gap-1.5">
            <input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="Output title (e.g. Course handbook)"
              className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
            />
            <input
              value={newSpec}
              onChange={(e) => setNewSpec(e.target.value)}
              placeholder="Spec — what 'good' looks like (optional)"
              className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
            />
            <div className="flex items-center gap-2">
              <select
                value={newKind}
                onChange={(e) => setNewKind(e.target.value as ProjectOutputKind)}
                className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-sm"
              >
                <option value="artifact">artifact</option>
                <option value="file">file</option>
                <option value="message">message</option>
                <option value="decision">decision</option>
                <option value="report">report</option>
                <option value="code">code</option>
              </select>
              <label className="flex items-center gap-1 text-xs text-[var(--color-muted)]">
                <input
                  type="checkbox"
                  checked={newRequired}
                  onChange={(e) => setNewRequired(e.target.checked)}
                />
                required
              </label>
              <button
                type="button"
                onClick={() => void add()}
                disabled={!newTitle.trim()}
                className="ml-auto rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] disabled:opacity-40"
              >
                Add output
              </button>
            </div>
          </div>
        </BusyRegion>
      ) : null}
    </section>
  );
}
