import { describe, expect, it } from "vitest";

import {
  WIZARD_STEPS,
  canLeaveInputs,
  hasAnyInput,
  inputsSummary,
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
