import { describe, expect, it } from "vitest";

import {
  ASK_SUGGESTIONS,
  CHANGE_KINDS,
  MAX_TURNS,
  capTurns,
  historyFor,
  isSendKey,
  loadThread,
  newTurnId,
  saveThread,
  sourceHref,
  sourceTab,
  storageKey,
  suggestedRequirement,
  updateTurn,
  withKindPrefix,
  type AskTurn,
} from "@/components/projects/ask/askThread";

const done = (i: number): AskTurn => ({
  id: `t${i}`,
  q: `q${i}`,
  status: "done",
  a: `a${i}`,
  sources: [],
});

function memStorage() {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
  };
}

describe("thread capping and history", () => {
  it("keeps the last MAX_TURNS turns", () => {
    const turns = Array.from({ length: 13 }, (_, i) => done(i));
    const capped = capTurns(turns);
    expect(capped).toHaveLength(MAX_TURNS);
    expect(capped[0].id).toBe("t3");
    expect(capTurns(turns.slice(0, 2))).toHaveLength(2);
  });

  it("sends only answered turns, the last six, oldest first", () => {
    const turns: AskTurn[] = [
      ...Array.from({ length: 8 }, (_, i) => done(i)),
      { id: "e", q: "broken", status: "error", error: "x" },
      { id: "p", q: "pending", status: "pending" },
    ];
    const h = historyFor(turns);
    expect(h).toHaveLength(6);
    expect(h[0]).toEqual({ q: "q2", a: "a2" });
    expect(h[5]).toEqual({ q: "q7", a: "a7" });
  });

  it("patches one turn by id", () => {
    const out = updateTurn([done(1), done(2)], "t2", { a: "new" });
    expect(out[0].a).toBe("a1");
    expect(out[1].a).toBe("new");
  });

  it("makes unique turn ids", () => {
    expect(newTurnId()).not.toBe(newTurnId());
  });
});

describe("sessionStorage persistence", () => {
  it("round-trips per project and drops in-flight turns", () => {
    const s = memStorage();
    saveThread(s, "a", [done(1), { id: "p", q: "?", status: "pending" }]);
    saveThread(s, "b", [done(9)]);
    expect(loadThread(s, "a").map((t) => t.id)).toEqual(["t1"]);
    expect(loadThread(s, "b").map((t) => t.id)).toEqual(["t9"]);
    expect(s.map.has(storageKey("a"))).toBe(true);
  });

  it("removes the key when the thread is empty", () => {
    const s = memStorage();
    saveThread(s, "a", [done(1)]);
    saveThread(s, "a", []);
    expect(s.map.has(storageKey("a"))).toBe(false);
  });

  it("survives junk, missing storage and throwing storage", () => {
    const s = memStorage();
    s.map.set(storageKey("a"), "{not json");
    expect(loadThread(s, "a")).toEqual([]);
    s.map.set(storageKey("a"), JSON.stringify({ not: "array" }));
    expect(loadThread(s, "a")).toEqual([]);
    expect(loadThread(null, "a")).toEqual([]);
    expect(() =>
      saveThread(
        {
          setItem: () => {
            throw new Error("quota");
          },
          removeItem: () => {},
        },
        "a",
        [done(1)],
      ),
    ).not.toThrow();
  });

  it("validates stored turns and their sources", () => {
    const s = memStorage();
    s.map.set(
      storageKey("a"),
      JSON.stringify([
        { id: "ok", q: "q", status: "done", a: "a", suggestion: "Add X", sources: [
          { kind: "card", id: "t_1", label: "Draft" },
          { kind: "secret", id: "x", label: "nope" },
          "junk",
        ] },
        { id: "err", q: "q", status: "error" },
        { id: "p", q: "q", status: "pending" },
        { q: "no id", status: "done", a: "a" },
        null,
      ]),
    );
    const turns = loadThread(s, "a");
    expect(turns.map((t) => t.id)).toEqual(["ok", "err"]);
    expect(turns[0].sources).toEqual([{ kind: "card", id: "t_1", label: "Draft" }]);
    expect(turns[0].suggestion).toBe("Add X");
    expect(turns[1].error).toBe("That did not go through.");
  });
});

describe("source links", () => {
  it("links cards and runs to their pages", () => {
    expect(sourceHref("my proj", { kind: "card", id: "t_a/b", label: "" })).toBe(
      "/projects/my%20proj/cards/t_a%2Fb",
    );
    expect(sourceHref("p", { kind: "run", id: "3", label: "" })).toBe("/projects/p/runs/3");
    expect(sourceTab({ kind: "card", id: "x", label: "" })).toBeNull();
  });

  it("links everything else to its tab", () => {
    expect(sourceHref("p", { kind: "output", id: "o1", label: "" })).toBe(
      "/projects/p?tab=outputs",
    );
    expect(sourceTab({ kind: "requirement", id: "1", label: "" })).toBe("iterations");
    expect(sourceTab({ kind: "plan", id: "1", label: "" })).toBe("plan");
    expect(sourceTab({ kind: "event", id: "1", label: "" })).toBe("board");
  });
});

describe("change mode", () => {
  it("prefixes the kind label", () => {
    expect(withKindPrefix("direction", "  Focus on HK  ")).toBe("Change direction: Focus on HK");
    expect(withKindPrefix(null, " plain ")).toBe("plain");
    expect(withKindPrefix("memory", "")).toBe("Add memory: ");
  });

  it("offers the five kinds and three suggestions", () => {
    expect(CHANGE_KINDS.map((k) => k.label)).toEqual([
      "Add requirement",
      "Change direction",
      "Change an output",
      "Attach file",
      "Add memory",
    ]);
    expect(ASK_SUGGESTIONS.map((s) => s.label)).toEqual([
      "What's left?",
      "What changed since the last run?",
      "Why is it slow?",
    ]);
  });

  it("pre-fills a requirement from the model's suggestion, else the question", () => {
    expect(suggestedRequirement({ ...done(1), suggestion: " Add a 3-year term " })).toBe(
      "Add a 3-year term",
    );
    expect(suggestedRequirement(done(1))).toBe("q1");
  });
});

describe("isSendKey", () => {
  it("sends on Enter only", () => {
    expect(isSendKey({ key: "Enter", shiftKey: false })).toBe(true);
    expect(isSendKey({ key: "Enter", shiftKey: true })).toBe(false);
    expect(isSendKey({ key: "a", shiftKey: false })).toBe(false);
    expect(isSendKey({ key: "Enter", shiftKey: false, isComposing: true })).toBe(false);
    expect(isSendKey({ key: "Enter", shiftKey: false, keyCode: 229 })).toBe(false);
  });
});
