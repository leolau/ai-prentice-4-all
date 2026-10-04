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

/**
 * Step 4's plan choice. `scope` (the default) lets the agent ask a few
 * clarifying questions first and drafts the plan once the owner confirms
 * its understanding; `agent` drafts right away; `self` drafts nothing.
 */
export type PlanChoice = "scope" | "agent" | "self";

export const PLAN_CHOICES: { value: PlanChoice; label: string; explain: string; create: string }[] = [
  {
    value: "scope",
    label: "Answer a few questions first, then the agent drafts the plan",
    explain:
      "The agent reads the brief and your inputs and asks 3–7 questions about the goal, scope and format. You land on its Scope tab; once you confirm its understanding it drafts the plan. Nothing runs.",
    create: "Create project and answer a few questions",
  },
  {
    value: "agent",
    label: "Let the agent draft the plan right away",
    explain:
      "The agent reads the brief and your inputs and proposes steps in the background; you land on the Plan tab and activate the plan when it looks right.",
    create: "Create project and draft the plan",
  },
  {
    value: "self",
    label: "I’ll write it myself",
    explain: "You land on the Plan tab with the readiness checklist and an empty Plan editor.",
    create: "Create project",
  },
];

export const DEFAULT_PLAN_CHOICE: PlanChoice = "scope";

export function planChoice(value: PlanChoice) {
  return PLAN_CHOICES.find((c) => c.value === value) ?? PLAN_CHOICES[0];
}

/** Where the person lands once the project (and its inputs) exist. */
export function planLanding(slug: string, choice: PlanChoice): string {
  const base = `/projects/${encodeURIComponent(slug)}`;
  return choice === "scope" ? `${base}?tab=scope` : `${base}#panel-plan`;
}
