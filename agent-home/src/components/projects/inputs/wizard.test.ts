import { describe, expect, it } from "vitest";

import {
  DEFAULT_PLAN_CHOICE,
  PLAN_CHOICES,
  WIZARD_STEPS,
  canLeaveInputs,
  hasAnyInput,
  inputsSummary,
  planChoice,
  planLanding,
  stepLabel,
} from "@/components/projects/inputs/wizard";

const NONE = { files: 0, memories: 0, links: 0 };

describe("wizard rules", () => {
  it("has four steps in the mockup's order", () => {
    expect(WIZARD_STEPS.map((s) => s.label)).toEqual([
      "What",
      "Inputs",
      "How it runs",
      "Review & plan",
    ]);
    expect(stepLabel(2)).toBe("Inputs");
  });

  it("the Inputs step needs something added or an explicit skip", () => {
    expect(canLeaveInputs(NONE, false)).toBe(false);
    expect(canLeaveInputs(NONE, true)).toBe(true);
    expect(canLeaveInputs({ ...NONE, files: 1 }, false)).toBe(true);
    expect(canLeaveInputs({ ...NONE, memories: 1 }, false)).toBe(true);
    expect(canLeaveInputs({ ...NONE, links: 1 }, false)).toBe(true);
    expect(hasAnyInput(NONE)).toBe(false);
  });

  it("summarises inputs in plain words", () => {
    expect(inputsSummary(NONE)).toBeNull();
    expect(inputsSummary({ files: 1, memories: 1, links: 1 })).toBe(
      "1 file · 1 memory · 1 link or note",
    );
    expect(inputsSummary({ files: 2, memories: 3, links: 0 })).toBe("2 files · 3 memories");
  });
});

describe("plan choice", () => {
  it("offers three choices, asking about scope first by default", () => {
    expect(PLAN_CHOICES.map((c) => c.value)).toEqual(["scope", "agent", "self"]);
    expect(DEFAULT_PLAN_CHOICE).toBe("scope");
    expect(planChoice("scope").label).toBe(
      "Answer a few questions first, then the agent drafts the plan",
    );
    expect(planChoice("agent").label).toBe("Let the agent draft the plan right away");
    expect(planChoice("self").label).toBe("I’ll write it myself");
  });

  it("names what each choice's create button does", () => {
    expect(planChoice("scope").create).toBe("Create project and answer a few questions");
    expect(planChoice("agent").create).toBe("Create project and draft the plan");
    expect(planChoice("self").create).toBe("Create project");
    expect(planChoice("scope").explain).toMatch(/3–7 questions/);
    expect(planChoice("scope").explain).toMatch(/Nothing runs/);
  });

  it("lands on Scope for the questions, on the Plan otherwise", () => {
    expect(planLanding("monday-digest", "scope")).toBe("/projects/monday-digest?tab=scope");
    expect(planLanding("monday-digest", "agent")).toBe("/projects/monday-digest#panel-plan");
    expect(planLanding("monday-digest", "self")).toBe("/projects/monday-digest#panel-plan");
    expect(planLanding("a b", "scope")).toBe("/projects/a%20b?tab=scope");
  });
});
