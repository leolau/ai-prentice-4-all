"use client";

/**
 * Shared next/previous navigation bar for search results.
 * Shows the current match index, total matches, and arrow buttons.
 */
export function SearchNav({
  current,
  total,
  onPrev,
  onNext,
  onClose,
}: {
  current: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-xs">
      <span className="text-[var(--color-muted)]">
        {total > 0 ? `${current + 1} / ${total}` : "No matches"}
      </span>
      <button
        type="button"
        onClick={onPrev}
        disabled={total === 0}
        className="rounded px-1.5 py-0.5 text-[var(--color-fg)] disabled:opacity-40 hover:bg-[var(--color-surface-2)]"
        aria-label="Previous match"
      >
        ↑
      </button>
      <button
        type="button"
        onClick={onNext}
        disabled={total === 0}
        className="rounded px-1.5 py-0.5 text-[var(--color-fg)] disabled:opacity-40 hover:bg-[var(--color-surface-2)]"
        aria-label="Next match"
      >
        ↓
      </button>
      <button
        type="button"
        onClick={onClose}
        className="ml-auto rounded px-1.5 py-0.5 text-[var(--color-muted)] hover:bg-[var(--color-surface-2)]"
        aria-label="Close search"
      >
        ×
      </button>
    </div>
  );
}
