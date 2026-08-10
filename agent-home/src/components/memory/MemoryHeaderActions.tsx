"use client";

import { memoryHeaderActionsRef } from "@/lib/memory/header-actions";

/**
 * The Legend button rendered in the `MobileShell` header bar, next to the
 * "Memory" title.  It used to live inside `MemoryMap` next to the plot;
 * moving it to the header gives the map the full column width and puts the
 * legend affordance in the upper-right corner where the eye expects it.
 *
 * The callback is populated by `MemoryMap` via the shared
 * `memoryHeaderActionsRef` — see `lib/memory/header-actions.ts` for the
 * rationale.  The `onClick` reads the ref at click time so it always invokes
 * the latest callback.
 */
export function MemoryHeaderActions() {
  return (
    <button
      data-component="MemoryHeaderActions"
      type="button"
      onClick={() => memoryHeaderActionsRef.current.openLegend()}
      aria-label="Map legend"
      title="How to read the map"
      className="shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-muted)]"
    >
      Legend
    </button>
  );
}
