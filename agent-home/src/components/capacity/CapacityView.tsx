import type { CapacityResponse } from "@/types";

const TONE: Record<CapacityResponse["state"], string> = {
  comfortable: "text-[var(--color-ok,#16a34a)]",
  watch: "text-[var(--color-warn,#d97706)]",
  constrained: "text-[var(--color-danger,#dc2626)]",
};

const VERDICT_HINT: Record<CapacityResponse["state"], string> = {
  comfortable: "Room for more people and more profiles.",
  watch: "A bound is approaching — plan, don't panic.",
  constrained: "Actively degrading — act now.",
};

function gb(mb: number | null): string {
  return mb === null ? "unknown" : `${(mb / 1024).toFixed(1)} GB`;
}

function seconds(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}s`;
}

/**
 * FG-31 — where the box stands, on the surface the owner actually uses (D20).
 *
 * Two things are deliberate. The verdict always shows **the bound that produced
 * it**, because "78%" tells the owner nothing about what to do while "memory,
 * driven by 9 concurrent conversations" does. And when the binding bound cannot
 * be moved by hardware, that is said before any recommendation — the SQLite
 * single-writer bound is serialisation, so a bigger box would be money spent
 * for no change.
 */
export function CapacityView({ capacity }: { capacity: CapacityResponse }) {
  const ind = capacity.indicators;
  const binding = capacity.binding_constraint;
  const rows: { label: string; value: string }[] = [
    {
      label: "Active conversations",
      value:
        ind.cap_box_wide === null
          ? `${ind.active_conversations} (no cap set)`
          : `${ind.active_conversations} / ~${ind.cap_box_wide}`,
    },
    {
      label: "Memory available",
      value: ind.total_mb ? `${gb(ind.available_mb)} of ${gb(ind.total_mb)}` : gb(ind.available_mb),
    },
    {
      label: "Write-lock waits",
      value:
        ind.write_lock_waits_per_hour === null
          ? "unknown"
          : `${ind.write_lock_waits_per_hour.toFixed(1)} / hour`,
    },
    {
      label: "Reply latency",
      value:
        ind.turn_samples === 0
          ? "no turns yet"
          : `p50 ${seconds(ind.turn_p50_s)} · p95 ${seconds(ind.turn_p95_s)}`,
    },
    { label: "Profiles", value: `${ind.profile_count}` },
  ];

  return (
    <div data-component="CapacityView" className="flex flex-col gap-4">
      <section className="flex flex-col gap-2 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <p className="text-sm text-[var(--color-muted)]">{capacity.summary}</p>
        <p className={`text-lg font-medium ${TONE[capacity.state]}`}>
          Headroom: {capacity.state}
        </p>
        <p className="text-sm text-[var(--color-muted)]">{VERDICT_HINT[capacity.state]}</p>
        {binding && capacity.state !== "comfortable" ? (
          <p data-component="CapacityBinding" className="text-sm">
            <span className="font-medium">{binding.name}</span> — {binding.reason}
          </p>
        ) : null}
        {binding && !binding.hardware_helps && capacity.state !== "comfortable" ? (
          <p
            data-component="CapacityHardwareWarning"
            className="rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm"
          >
            A bigger box will not fix this one.
          </p>
        ) : null}
      </section>

      <section
        data-component="CapacityIndicators"
        className="flex flex-col gap-2 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
      >
        <h2 className="text-sm font-medium">The reading</h2>
        <dl className="flex flex-col gap-2 text-sm">
          {rows.map((row) => (
            <div key={row.label} className="flex items-baseline justify-between gap-3">
              <dt className="text-[var(--color-muted)]">{row.label}</dt>
              <dd className="text-right">{row.value}</dd>
            </div>
          ))}
        </dl>
        {Object.keys(ind.per_profile).length > 1 ? (
          <p data-component="CapacityPerProfile" className="text-xs text-[var(--color-muted)]">
            Per profile:{" "}
            {Object.entries(ind.per_profile)
              .map(([name, count]) => `${name} ${count}`)
              .join(" · ")}
          </p>
        ) : null}
        {capacity.unavailable.length > 0 ? (
          <p data-component="CapacityUnavailable" className="text-xs text-[var(--color-muted)]">
            Not measured: {capacity.unavailable.join(", ")}
          </p>
        ) : null}
      </section>

      {capacity.recommendations.length > 0 ? (
        <section
          data-component="CapacityRecommendations"
          className="flex flex-col gap-2 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
        >
          <h2 className="text-sm font-medium">What to do, cheapest first</h2>
          <ul className="flex flex-col gap-2 text-sm">
            {capacity.recommendations.map((rec) => (
              <li key={rec} className="rounded-lg bg-[var(--color-surface-2)] px-3 py-2">
                {rec}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
