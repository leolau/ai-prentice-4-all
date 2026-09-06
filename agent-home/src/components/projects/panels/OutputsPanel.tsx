"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { friendlyError } from "@/components/projects/errors";

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
  const router = useRouter();
  const [outputs, setOutputs] = useState(initial);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offersClosure, setOffersClosure] = useState(false);
  const [closing, setClosing] = useState(false);
  const [closed, setClosed] = useState(false);

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
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(
          friendlyError(
            { status: res.status, detail: data.detail },
            "That didn't stick — try again.",
          ),
        );
      }
      // The accept route answers with the updated row + the closure offer;
      // merge the row (the joined deliveries survive the spread) so the
      // Accept button disappears without a reload.
      const payload = (await res.json()) as {
        output?: Partial<ProjectOutputWithDeliveries>;
        offers_closure?: boolean;
      };
      setOutputs((prev) => applyAcceptEnvelope(prev, outputId, payload).outputs);
      if (payload.offers_closure === true) setOffersClosure(true);
      // Progress, health and the header rollup are derived on the server
      // read; revalidate so they move with the row.
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't stick — try again.");
    } finally {
      setBusyId(null);
    }
  };

  const markDone = async () => {
    setClosing(true);
    setError(null);
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(slug)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ status: "done" }),
      });
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) {
        throw new Error(
          friendlyError(
            { status: res.status, detail: data.detail },
            "The project could not be marked done.",
          ),
        );
      }
      setClosed(true);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The project could not be marked done.");
    } finally {
      setClosing(false);
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
      if (!res.ok) throw new Error(friendlyError({ status: res.status, detail: data.detail }, "Could not add the output."));
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
      if (!res.ok) throw new Error(friendlyError({ status: res.status, detail: data.detail }, "Could not remove the output."));
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
          {closed ? (
            "This project is marked done. It stays on the record; archive it from the ⋯ menu when you want it off the list."
          ) : (
            <>
              Every required output is now accepted — this project can be
              closed. Mark it done here, or keep it open for another run.
              <span className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void markDone()}
                  disabled={closing}
                  className="rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                >
                  {closing ? "Marking done…" : "Mark project done"}
                </button>
                <button
                  type="button"
                  onClick={() => setOffersClosure(false)}
                  disabled={closing}
                  className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs disabled:opacity-50"
                >
                  Keep open
                </button>
              </span>
            </>
          )}
        </p>
      ) : null}

      {sorted.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          No outputs yet. Add one below — the deliverable is what a run is
          accountable to, and what you accept when it is done.
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
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void accept(output.id)}
                      className="rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)]"
                    >
                      Accept
                    </button>
                    <span className="text-xs text-[var(--color-muted)]">
                      Delivered — waiting for you to judge it met the spec. Accepting is
                      a human act; the agent cannot do it for you.
                    </span>
                  </div>
                </BusyRegion>
              ) : null}
              {output.status === "accepted" && output.accepted_at != null ? (
                <p className="mt-1 text-xs text-[var(--color-muted)]">
                  accepted {dateTimeLabel(output.accepted_at)}
                  {output.accepted_by ? ` by ${output.accepted_by}` : ""}
                </p>
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
