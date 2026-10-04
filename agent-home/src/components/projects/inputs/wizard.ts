/**
 * Pure rules for the four-step new-project wizard: the step list, and when
 * the Inputs step may be left (something added, or an explicit skip).
 */

export type WizardStep = 1 | 2 | 3 | 4;

export const WIZARD_STEPS: { step: WizardStep; label: string }[] = [
  { step: 1, label: "What" },
  { step: 2, label: "Inputs" },
  { step: 3, label: "How it runs" },
  { step: 4, label: "Review & plan" },
];

export function stepLabel(step: WizardStep): string {
  return WIZARD_STEPS.find((s) => s.step === step)?.label ?? "";
}

export interface InputsTally {
  files: number;
  memories: number;
  links: number;
}

export function hasAnyInput(tally: InputsTally): boolean {
  return tally.files + tally.memories + tally.links > 0;
}

/** Step 2 must be answered: add something, or say there is nothing. */
export function canLeaveInputs(tally: InputsTally, skipped: boolean): boolean {
  return skipped || hasAnyInput(tally);
}

/** "2 files · 1 memory · 1 link or note", or null when there is nothing. */
export function inputsSummary(tally: InputsTally): string | null {
  const parts: string[] = [];
  if (tally.files) parts.push(`${tally.files} file${tally.files === 1 ? "" : "s"}`);
  if (tally.memories) {
    parts.push(`${tally.memories} memor${tally.memories === 1 ? "y" : "ies"}`);
  }
  if (tally.links) {
    parts.push(`${tally.links} link${tally.links === 1 ? "" : "s"} or note${tally.links === 1 ? "" : "s"}`);
  }
  return parts.length ? parts.join(" · ") : null;
}
