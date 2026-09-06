import type { ProjectOutputRollup } from "@/types";

/**
 * One phrase for the state of the deliverables — "2 of 3 delivered ·
 * 1 to accept" — shared by the list row and the progress panel so they
 * never disagree. Null when the project declares nothing.
 */
export function outputsLabel(rollup: ProjectOutputRollup | undefined): string | null {
  if (!rollup || rollup.total === 0) return null;
  const parts = [`${rollup.accepted} of ${rollup.total} outputs accepted`];
  if (rollup.awaiting_acceptance > 0) {
    parts.push(
      `${rollup.awaiting_acceptance} to accept`,
    );
  } else if (rollup.delivered < rollup.total) {
    parts.push(`${rollup.total - rollup.delivered} not delivered yet`);
  }
  return parts.join(" · ");
}
