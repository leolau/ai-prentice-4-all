"use client";

import { useEffect } from "react";

import { isChunkLoadError, reloadOnceForChunkError } from "@/lib/chunk-reload";
import "./globals.css";

/**
 * The root error boundary (Next.js App Router convention — replaces the
 * whole layout, so it re-declares `<html>`/`<body>`).
 *
 * Catches the render-time half of a stale-bundle chunk-load failure (a
 * `React.lazy`/dynamic import throwing during render); the other half —
 * a background prefetch's import failing outside React's tree — is
 * `ChunkErrorListener`'s job, since this boundary never sees those.
 * A genuine application error still gets a plain, honest fallback
 * instead of Next's default "Application error" page, which named
 * nothing and pointed nowhere (the report that led here).
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const isStaleBundle = isChunkLoadError(error);

  useEffect(() => {
    reloadOnceForChunkError(error);
  }, [error]);

  if (isStaleBundle) {
    // The reload above is already in flight — nothing useful to show for
    // the instant before it lands, and no button that would just throw
    // the same error again.
    return (
      <html lang="en" data-theme="dark">
        <body />
      </html>
    );
  }

  return (
    <html lang="en" data-theme="dark">
      <body
        data-component="GlobalError"
        className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[var(--color-bg)] p-6 text-center text-[var(--color-text)]"
      >
        <h1 className="text-lg font-semibold">Something went wrong</h1>
        <p className="max-w-sm text-sm text-[var(--color-muted)]">
          Reloading usually fixes this. If it keeps happening, let us know
          what you were doing.
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => reset()}
            className="rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm"
          >
            Try again
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm text-[var(--color-accent-fg)]"
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
