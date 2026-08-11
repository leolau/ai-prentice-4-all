import { Spinner } from "@/components/ui/Spinner";

/**
 * The placeholder every route's `loading.tsx` renders.
 *
 * Every page in agent-home is a `force-dynamic` server component that awaits
 * several upstream calls before it can render anything, so without a route-level
 * loading UI a tap on the nav left the *previous* page on screen, frozen, until
 * the server replied — the app read as slow and unresponsive even when it was
 * merely working. Paired with a `loading.tsx` per segment, the shell and this
 * skeleton paint immediately instead.
 *
 * `rows` should roughly match the shape of the real page so the swap isn't a
 * jarring reflow.
 */
export function PageSkeleton({
  rows = 4,
  label = "Loading…",
}: {
  rows?: number;
  label?: string;
}) {
  return (
    <div data-component="PageSkeleton" className="flex flex-col gap-4">
      <p
        role="status"
        aria-live="polite"
        className="inline-flex items-center gap-2 self-start rounded-2xl border border-[var(--color-accent)] bg-[var(--color-surface-2)] px-3 py-2 text-sm font-medium text-[var(--color-accent)]"
      >
        <Spinner />
        {label}
      </p>
      <div aria-hidden="true" className="flex animate-pulse flex-col gap-3">
        {Array.from({ length: rows }, (_, i) => (
          <div
            key={i}
            className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <div className="h-3 w-1/3 rounded bg-[var(--color-surface-2)]" />
            <div className="mt-3 h-3 w-full rounded bg-[var(--color-surface-2)]" />
            <div className="mt-2 h-3 w-4/5 rounded bg-[var(--color-surface-2)]" />
          </div>
        ))}
      </div>
    </div>
  );
}
