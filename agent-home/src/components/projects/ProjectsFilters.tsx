"use client";

import {
  FILTER_CHIPS,
  type ProjectsFilterState,
} from "@/components/projects/filters";

/**
 * Search and the seven view chips (§13). The chips are single-select on
 * purpose — each names one slice of the list, and slicing by status *and*
 * cadence at once is a query nobody types; the URL still carries the exact
 * parameters for anyone who wants one.
 */
export function ProjectsFilters({
  value,
  onChange,
}: {
  value: ProjectsFilterState;
  onChange: (next: ProjectsFilterState) => void;
}) {
  return (
    <div data-component="ProjectsFilters" className="flex flex-col gap-2">
      <input
        type="search"
        value={value.q}
        onChange={(e) => onChange({ ...value, q: e.target.value })}
        placeholder="Search projects"
        className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
      />

      <div className="flex flex-wrap gap-1.5 text-[11px]">
        {FILTER_CHIPS.map((chip) => (
          <button
            key={chip.view}
            type="button"
            onClick={() => onChange({ ...value, view: chip.view })}
            aria-pressed={value.view === chip.view}
            className={`rounded-full border px-2 py-1 transition ${
              value.view === chip.view
                ? "border-[var(--color-accent)] text-[var(--color-accent)]"
                : "border-[var(--color-border)] text-[var(--color-muted)]"
            }`}
          >
            {chip.label}
          </button>
        ))}
      </div>
    </div>
  );
}
