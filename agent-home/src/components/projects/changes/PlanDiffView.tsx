import type { PlanDiff } from "@/components/projects/changes/planDiff";

const ROW_CLASS: Record<string, string> = {
  removed: "border-red-500/40 bg-red-500/10 text-red-300 line-through",
  added: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  changed: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  moved: "border-emerald-500/30 bg-emerald-500/5",
  same: "border-[var(--color-border)]",
};

const ROW_MARK: Record<string, string> = {
  removed: "−",
  added: "+",
  changed: "~",
  moved: "↕",
  same: "",
};

/** The active plan vs the agent's draft: removed struck through in red, new and changed in green. */
export function PlanDiffView({ diff }: { diff: PlanDiff }) {
  const numbers = diff.rows.reduce<number[]>((acc, row) => {
    const last = acc.length ? acc[acc.length - 1] : 0;
    acc.push(row.kind === "removed" ? last : last + 1);
    return acc;
  }, []);
  return (
    <div data-component="PlanDiffView" className="flex flex-col gap-1.5">
      {diff.unchanged ? (
        <p className="text-sm text-[var(--color-muted)]">The plan stays the same.</p>
      ) : (
        <p className="text-xs text-[var(--color-muted)]">
          {[
            diff.added && `${diff.added} added`,
            diff.changed && `${diff.changed} changed`,
            diff.removed && `${diff.removed} removed`,
            diff.moved && `${diff.moved} moved`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      )}
      <ol className="flex flex-col gap-1.5">
        {diff.rows.map((row, i) => {
          const n = numbers[i];
          return (
            <li
              key={`${row.kind}:${row.key}`}
              data-diff={row.kind}
              className={`flex gap-2 rounded-lg border px-2.5 py-1.5 text-sm ${ROW_CLASS[row.kind]}`}
            >
              <span aria-hidden className="w-4 shrink-0 text-center">
                {ROW_MARK[row.kind]}
              </span>
              <span className="min-w-0 flex-1 break-words">
                {row.kind === "removed" ? "" : `${n}. `}
                {row.step.title}
                {row.step.checkpoint ? " ⏸" : ""}
                {row.kind === "changed" && row.before && row.before.title !== row.step.title ? (
                  <span className="block text-xs text-[var(--color-muted)] line-through">
                    {row.before.title}
                  </span>
                ) : null}
              </span>
              {row.kind !== "same" ? (
                <span className="sr-only">
                  {row.kind === "removed" ? "removed" : row.kind === "added" ? "added" : row.kind}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
