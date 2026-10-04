"use client";

import {
  PROJECT_TABS,
  PROJECT_TAB_LABEL,
  type ProjectTab,
} from "@/components/projects/tabs/types";

export function ProjectTabsNav({
  active,
  onSelect,
  badges = {},
}: {
  active: ProjectTab;
  onSelect: (tab: ProjectTab) => void;
  /** e.g. `{ board: 3 }` — how many things on that tab need a person. */
  badges?: Partial<Record<ProjectTab, number>>;
}) {
  return (
    <nav
      data-component="ProjectTabsNav"
      aria-label="Project sections"
      className="-mx-1 overflow-x-auto border-b border-[var(--color-border)] px-1"
    >
      <ul role="tablist" className="flex w-max gap-1">
        {PROJECT_TABS.map((tab) => {
          const on = tab === active;
          const badge = badges[tab] ?? 0;
          return (
            <li key={tab}>
              <button
                type="button"
                role="tab"
                aria-selected={on}
                data-tab={tab}
                onClick={() => onSelect(tab)}
                className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm whitespace-nowrap ${
                  on
                    ? "border-[var(--color-accent)] font-semibold"
                    : "border-transparent text-[var(--color-muted)]"
                }`}
              >
                {PROJECT_TAB_LABEL[tab]}
                {badge > 0 ? (
                  <span className="rounded-full bg-red-500 px-1.5 text-[10px] font-semibold text-white">
                    {badge}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
