import type { BarSegment, StandStep, StepState } from "@/components/projects/dashboard/whereItStands";
import type { CardsBar, OutputsBar } from "@/components/projects/dashboard/whereItStands";

const STEP_MARK: Record<StepState, string> = {
  done: "✓",
  attention: "!",
  current: "●",
  todo: "",
};

const STEP_CLASS: Record<StepState, string> = {
  done: "border-emerald-500/60 bg-emerald-500/15 text-emerald-300",
  attention: "border-amber-500/70 bg-amber-500/15 text-amber-300",
  current: "border-[var(--color-accent)] bg-[var(--color-accent)]/15 text-[var(--color-accent)]",
  todo: "border-[var(--color-border)] text-[var(--color-muted)]",
};

const SEGMENT_CLASS: Record<string, string> = {
  accepted: "bg-emerald-400",
  delivered: "bg-sky-400",
  in_progress: "bg-[var(--color-accent)]/60",
  done: "bg-emerald-400",
  working: "bg-sky-400",
  needs: "bg-amber-400",
};

function Bar({ label, summary, segments }: { label: string; summary: string; segments: BarSegment[] }) {
  return (
    <div className="mt-3">
      <p className="flex justify-between gap-2 text-xs">
        <span className="text-[var(--color-muted)]">{label}</span>
        <span>{summary}</span>
      </p>
      <div
        role="img"
        aria-label={`${label}: ${segments.map((s) => `${s.count} ${s.label}`).join(", ")}`}
        className="mt-1 flex h-2 overflow-hidden rounded-full bg-[var(--color-surface-2)]"
      >
        {segments.map((segment) =>
          segment.percent > 0 ? (
            <span
              key={segment.key}
              data-segment={segment.key}
              className={SEGMENT_CLASS[segment.key] ?? "bg-[var(--color-muted)]"}
              style={{ width: `${segment.percent}%` }}
            />
          ) : null,
        )}
      </div>
    </div>
  );
}

/** Stage stepper plus the outputs and current-iteration bars. */
export function WhereItStands({
  steps,
  outputs,
  cards,
}: {
  steps: StandStep[];
  outputs: OutputsBar;
  cards: CardsBar | null;
}) {
  return (
    <section
      data-component="WhereItStands"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-sm font-semibold">Where it stands</h2>
      <ol className="mt-3 grid grid-cols-4 gap-y-3 sm:grid-cols-7">
        {steps.map((step) => (
          <li
            key={step.key}
            data-step={step.key}
            data-state={step.state}
            className="flex flex-col items-center gap-1 text-center"
          >
            <span
              aria-hidden
              className={`flex h-6 w-6 items-center justify-center rounded-full border text-xs ${STEP_CLASS[step.state]}`}
            >
              {STEP_MARK[step.state]}
            </span>
            <span className={`text-xs ${step.state === "todo" ? "text-[var(--color-muted)]" : "font-medium"}`}>
              {step.label}
            </span>
            {step.sub ? (
              <span className="text-[11px] leading-tight text-[var(--color-muted)]">{step.sub}</span>
            ) : null}
          </li>
        ))}
      </ol>
      {outputs.total > 0 ? (
        <Bar label="Outputs" summary={outputs.summary} segments={outputs.segments} />
      ) : (
        <p className="mt-3 text-xs text-[var(--color-muted)]">{outputs.summary}</p>
      )}
      {cards && cards.total > 0 ? (
        <Bar label="This iteration" summary={cards.summary} segments={cards.segments} />
      ) : null}
      <p data-component="StandLegend" className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[var(--color-muted)]">
        <span><span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-400" />accepted / done</span>
        <span><span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full bg-sky-400" />delivered / working</span>
        <span><span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full bg-amber-400" />needs you</span>
      </p>
    </section>
  );
}
