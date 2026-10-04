import { describe, expect, it } from "vitest";

import { planDiff } from "@/components/projects/changes/planDiff";
import type { PlaybookStep } from "@/types";

const s = (key: string, title = key, over: Partial<PlaybookStep> = {}): PlaybookStep => ({
  key,
  title,
  ...over,
});

const kinds = (d: ReturnType<typeof planDiff>) => d.rows.map((r) => `${r.kind}:${r.key}`);

describe("planDiff", () => {
  it("no-op: identical plans are unchanged", () => {
    const steps = [s("a"), s("b", "B", { depends_on: ["a"] }), s("c")];
    const d = planDiff(steps, steps.map((x) => ({ ...x })));
    expect(d.unchanged).toBe(true);
    expect(kinds(d)).toEqual(["same:a", "same:b", "same:c"]);
  });

  it("both empty is a no-op", () => {
    expect(planDiff([], []).unchanged).toBe(true);
  });

  it("add: new steps appear in drafted order", () => {
    const d = planDiff([s("a"), s("c")], [s("a"), s("b"), s("c"), s("d")]);
    expect(kinds(d)).toEqual(["same:a", "added:b", "same:c", "added:d"]);
    expect(d.added).toBe(2);
    expect(d.unchanged).toBe(false);
  });

  it("remove: dropped steps stay where they were", () => {
    const d = planDiff([s("a"), s("b"), s("c"), s("d")], [s("a"), s("c")]);
    expect(kinds(d)).toEqual(["same:a", "removed:b", "same:c", "removed:d"]);
    expect(d.removed).toBe(2);
  });

  it("remove everything / add everything", () => {
    expect(kinds(planDiff([s("a")], []))).toEqual(["removed:a"]);
    expect(kinds(planDiff([], [s("a")]))).toEqual(["added:a"]);
  });

  it("change: title, body, checkpoint or dependencies differ", () => {
    const d = planDiff(
      [s("a"), s("b", "Draft MOU"), s("c"), s("e", "E", { depends_on: ["a"] })],
      [
        s("a", "a", { body: "now with notes" }),
        s("b", "Re-draft MOU"),
        s("c", "c", { checkpoint: true }),
        s("e", "E", { depends_on: ["c"] }),
      ],
    );
    expect(kinds(d)).toEqual(["changed:a", "changed:b", "changed:c", "changed:e"]);
    expect(d.rows[1].before?.title).toBe("Draft MOU");
    expect(d.rows[1].moved).toBe(false);
    expect(d.changed).toBe(4);
  });

  it("reorder: only the step that left its place is moved", () => {
    const d = planDiff([s("a"), s("b"), s("c"), s("d")], [s("a"), s("c"), s("d"), s("b")]);
    expect(kinds(d)).toEqual(["same:a", "same:c", "same:d", "moved:b"]);
    expect(d.moved).toBe(1);
    expect(d.unchanged).toBe(false);
  });

  it("a changed step that also moved is flagged", () => {
    const d = planDiff([s("a"), s("b"), s("c")], [s("c"), s("a"), s("b", "B2")]);
    const b = d.rows.find((r) => r.key === "b")!;
    const c = d.rows.find((r) => r.key === "c")!;
    expect(b.kind).toBe("changed");
    expect(b.moved).toBe(false);
    expect(c.kind).toBe("moved");
  });

  it("matches a re-keyed step by its title", () => {
    const d = planDiff([s("gather", "Collect arrivals")], [s("collect", "Collect arrivals")]);
    expect(kinds(d)).toEqual(["same:collect"]);
  });

  it("mixes add, remove and change in one diff", () => {
    const d = planDiff(
      [s("confirm"), s("draft", "Draft each MOU (2.5%)"), s("review"), s("upload")],
      [s("confirm"), s("draft", "Re-draft each MOU (3%)"), s("review"), s("upload-v3")],
    );
    expect(kinds(d)).toEqual([
      "same:confirm",
      "changed:draft",
      "same:review",
      "removed:upload",
      "added:upload-v3",
    ]);
  });
});
