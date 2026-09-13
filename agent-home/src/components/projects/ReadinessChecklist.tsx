import type { ReadinessItem } from "@/components/projects/readiness";

/**
 * The header's answer to "why can't I run this?" — every precondition the
 * run gate checks, numbered in the order a person would clear them, each
 * with either a link to the panel that fixes it or (for "status") a
 * one-click Activate button right here — no need to go find where that
 * button lives. Hidden once the project is runnable so a healthy project
 * keeps a quiet header.
 */
export function ReadinessChecklist({
  items,
  findings,
  onActivate,
  activating = false,
}: {
  items: ReadinessItem[];
  findings: { code: string; severity: string; text: string }[];
  /** Wired up for the "status" item so Activate is a click, not a hunt. */
  onActivate?: () => void;
  activating?: boolean;
}) {
  const missing = items.filter((item) => !item.ok);
  if (missing.length === 0 && findings.length === 0) return null;
  let step = 0;
  return (
    <div
      data-component="ReadinessChecklist"
      className="mt-3 rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-surface-2)] p-3"
    >
      {missing.length > 0 ? (
        <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
          Before it can run — in order
        </p>
      ) : null}
      <ol className="mt-1 flex flex-col gap-1.5">
        {items.map((item) => {
          if (!item.ok) step += 1;
          return (
            <li key={item.key} className="flex items-start gap-2 text-sm">
              <span
                aria-hidden
                className={
                  item.ok
                    ? "text-emerald-400"
                    : "text-amber-400 tabular-nums"
                }
              >
                {item.ok ? "✓" : `${step}.`}
              </span>
              <span className="min-w-0 flex-1">
                <span className={item.ok ? "text-[var(--color-muted)]" : ""}>
                  {item.label}
                </span>
                {!item.ok ? (
                  <>
                    {" — "}
                    {item.key === "status" && onActivate ? (
                      <button
                        type="button"
                        onClick={onActivate}
                        disabled={activating}
                        className="text-[var(--color-accent)] underline-offset-2 hover:underline disabled:opacity-50"
                      >
                        {activating ? "Activating…" : "Activate now"}
                      </button>
                    ) : (
                      <a
                        href={item.anchor}
                        className="text-[var(--color-accent)] underline-offset-2 hover:underline"
                      >
                        {item.hint}
                      </a>
                    )}
                  </>
                ) : null}
              </span>
            </li>
          );
        })}
        {findings.map((finding) => (
          <li
            key={finding.code}
            className="flex items-start gap-2 text-sm"
            data-severity={finding.severity}
          >
            <span
              aria-hidden
              className={
                finding.severity === "stalled" ? "text-red-400" : "text-amber-400"
              }
            >
              !
            </span>
            <span className="min-w-0 flex-1">{finding.text}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
