"use client";

import { STAGE_LABEL } from "@/components/todos/TodoRow";
import type { TodosFilterState } from "@/components/todos/filters";
import type { TodosFacets, TodoStage } from "@/types";

const STAGE_ORDER: TodoStage[] = [
  "staged",
  "open",
  "working",
  "done",
  "dismissed",
];

const PRIORITY_ORDER = ["critical", "high", "normal", "low"];

/**
 * Search, stage chips, priority chips and the snooze switch.
 *
 * Priority chips come from the facets rather than the vocabulary, for the same
 * reason the inbox's channel chips do: offering a "critical" filter to someone
 * who has never had a critical to-do is a control that can only disappoint.
 * Stage chips are the exception — they are the lifecycle itself, and hiding
 * "Done" until the first to-do is done would hide where finished work goes.
 */
export function TodosFilters({
  facets,
  value,
  onChange,
}: {
  facets: TodosFacets;
  value: TodosFilterState;
  onChange: (next: TodosFilterState) => void;
}) {
  const counts = new Map(facets.stages.map((s) => [s.value, s.count]));
  const priorities = PRIORITY_ORDER.filter((p) =>
    facets.priorities.some((facet) => facet.value === p),
  );

  const toggle = (list: string[], item: string) =>
    list.includes(item) ? list.filter((x) => x !== item) : [...list, item];

  return (
    <div data-component="TodosFilters" className="flex flex-col gap-2">
      <input
        type="search"
        value={value.q}
        onChange={(e) => onChange({ ...value, q: e.target.value })}
        placeholder="Search to-dos"
        className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
      />

      <div className="flex flex-wrap gap-1.5 text-[11px]">
        {STAGE_ORDER.map((stage) => (
          <Chip
            key={stage}
            label={
              counts.get(stage)
                ? `${STAGE_LABEL[stage]} ${counts.get(stage)}`
                : STAGE_LABEL[stage]
            }
            on={value.stages.includes(stage)}
            onClick={() =>
              onChange({ ...value, stages: toggle(value.stages, stage) })
            }
          />
        ))}
      </div>

      {priorities.length > 0 ? (
        <div className="flex flex-wrap gap-1.5 text-[11px]">
          {priorities.map((priority) => (
            <Chip
              key={priority}
              label={priority}
              on={value.priorities.includes(priority)}
              onClick={() =>
                onChange({
                  ...value,
                  priorities: toggle(value.priorities, priority),
                })
              }
            />
          ))}
          <Chip
            label="Snoozed"
            on={value.includeSnoozed}
            onClick={() =>
              onChange({ ...value, includeSnoozed: !value.includeSnoozed })
            }
          />
        </div>
      ) : null}

      {value.sourceRef ? (
        <button
          type="button"
          onClick={() => onChange({ ...value, sourceRef: "" })}
          className="self-start rounded-full border border-[var(--color-accent)] px-2 py-1 text-[11px] text-[var(--color-accent)]"
        >
          From one arrival ✕
        </button>
      ) : null}
    </div>
  );
}

function Chip({
  label,
  on,
  onClick,
}: {
  label: string;
  on: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={`rounded-full border px-2 py-1 transition ${
        on
          ? "border-[var(--color-accent)] text-[var(--color-accent)]"
          : "border-[var(--color-border)] text-[var(--color-muted)]"
      }`}
    >
      {label}
    </button>
  );
}
