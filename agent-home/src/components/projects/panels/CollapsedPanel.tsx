"use client";

import { useEffect, useState, type ReactNode } from "react";

/**
 * An empty panel folded to one line (§13): "Files — none yet" with the
 * whole panel (and its Add form) behind a disclosure. Twelve stacked
 * panels are long on a phone, and an empty one says nothing worth the
 * height. The anchor id stays on the wrapper so the sticky nav still lands
 * here — and landing on it unfolds it.
 */
export function CollapsedPanel({
  anchor,
  label,
  hint,
  children,
}: {
  anchor: string;
  label: string;
  hint: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const check = () => {
      if (window.location.hash === `#${anchor}`) setOpen(true);
    };
    check();
    window.addEventListener("hashchange", check);
    return () => window.removeEventListener("hashchange", check);
  }, [anchor]);

  return (
    <div
      // The unfolded panel carries the anchor id itself.
      id={open ? undefined : anchor}
      data-component="CollapsedPanel"
      className="rounded-2xl border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]"
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left"
      >
        <span className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
          {label}
        </span>
        <span className="text-xs text-[var(--color-muted)]">
          {open ? "hide" : hint}
        </span>
      </button>
      {open ? <div className="border-t border-[var(--color-border)]">{children}</div> : null}
    </div>
  );
}
