/**
 * Module-level mutable ref that bridges the server-component page boundary.
 *
 * The memory page (`app/memory/page.tsx`) is a server component — it renders
 * `MobileShell` whose header holds `MemoryHeaderActions` (a client component),
 * while the `legendOpen` state lives inside `MemoryMap` (also client, but
 * rendered in the shell's `main`, below the header).  The two are siblings, so
 * props/context can't reach upward.
 *
 * `MemoryMap` writes its `openLegend` callback into
 * `memoryHeaderActionsRef.current` on mount (via `useEffect`);
 * `MemoryHeaderActions` reads it at click time.  No context provider, no
 * client wrapper, no store — just a stable ref the two components share.
 *
 * Safe because: only one `MemoryMap` instance exists at a time; the ref
 * object itself never changes identity; mutation doesn't trigger re-renders;
 * and `MemoryMap` resets the callback to noop on unmount so a stale ref
 * can't fire after navigation away.
 *
 * Same pattern as `lib/chat/header-actions.ts`.
 */
export interface MemoryHeaderActionsRef {
  openLegend: () => void;
}

const noop = () => {};

export const memoryHeaderActionsRef: { current: MemoryHeaderActionsRef } = {
  current: { openLegend: noop },
};
