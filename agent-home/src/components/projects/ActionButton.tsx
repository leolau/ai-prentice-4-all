"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

/**
 * A button bound to a `useProjectAction`: disabled and relabelled from the
 * first click until the refreshed page lands, so it can never look idle
 * while the backend is acting.
 */
export function ActionButton({
  busy,
  pendingLabel,
  children,
  disabled,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  busy: boolean;
  /** What the button says while in flight, e.g. "Starting run…". */
  pendingLabel?: ReactNode;
}) {
  return (
    <button
      type="button"
      {...rest}
      disabled={busy || disabled}
      aria-busy={busy || undefined}
      data-busy={busy ? "true" : undefined}
      className={
        className ??
        "rounded-xl border border-[var(--color-border)] px-3 py-1.5 text-sm disabled:opacity-50"
      }
    >
      {busy ? (
        <span className="inline-flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-r-transparent"
          />
          {pendingLabel ?? children}
        </span>
      ) : (
        children
      )}
    </button>
  );
}
