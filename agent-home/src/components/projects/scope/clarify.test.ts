import { describe, expect, it } from "vitest";

import {
  agreedUnderstanding,
  answerProgress,
  answersPayload,
  clarifyPath,
  emptyClarifyState,
  isClarifyState,
  latestRound,
  progressLine,
  roundQuestions,
  scopeView,
  selectedOptions,
  toggleOption,
} from "@/components/projects/scope/clarify";
import {
  answeredState,
  clarifyState,
  confirmedState,
  openState,
  question,
  round,
} from "@/components/projects/scope/__fixtures__/clarify";

describe("clarifyPath", () => {
  it("encodes the slug and appends the action", () => {
    expect(clarifyPath("a b")).toBe("/api/projects/a%20b/clarify");
    expect(clarifyPath("p", "confirm")).toBe("/api/projects/p/clarify/confirm");
  });
});

describe("emptyClarifyState / isClarifyState", () => {
  it("builds a not-started state from the summary", () => {
    const s = emptyClarifyState({
      status: "not_started",
      round: 0,
      open_count: 0,
      answered_count: 0,
      understanding: null,
      confirmed_at: null,
    });
    expect(s.rounds).toEqual([]);
    expect(s.job.status).toBe("idle");
    expect(isClarifyState(s)).toBe(true);
  });

  it("rejects payloads that are not a state", () => {
    expect(isClarifyState(null)).toBe(false);
    expect(isClarifyState({ status: "open" })).toBe(false);
    expect(isClarifyState({ detail: "nope" })).toBe(false);
  });
});

describe("scopeView", () => {
  it("maps each state to a screen", () => {
    expect(scopeView(clarifyState())).toBe("not_started");
    expect(scopeView(openState())).toBe("open");
    expect(scopeView(answeredState())).toBe("answered");
    expect(scopeView(confirmedState())).toBe("confirmed");
  });

  it("treats a done round with no questions as answered", () => {
    expect(scopeView(clarifyState({ rounds: [round({ done: true, status: "answered" })] }))).toBe("answered");
    expect(scopeView(clarifyState({ rounds: [round({ done: true, status: "open" })] }))).toBe("answered");
  });

  it("a newer round wins over an older confirmed one", () => {
    const s = confirmedState();
    const next = {
      ...s,
      rounds: [...s.rounds, round({ round_no: 2 })],
      questions: [...s.questions, question({ id: "q9", round_no: 2 })],
    };
    expect(latestRound(next)?.round_no).toBe(2);
    expect(scopeView(next)).toBe("open");
    expect(agreedUnderstanding(next)).toBe("Four MOUs for legal, in Word and PDF.");
  });
});

describe("roundQuestions", () => {
  it("filters by round and sorts by position", () => {
    const s = clarifyState({
      questions: [
        question({ id: "b", position: 1 }),
        question({ id: "x", round_no: 2 }),
        question({ id: "a", position: 0 }),
      ],
    });
    expect(roundQuestions(s, 1).map((q) => q.id)).toEqual(["a", "b"]);
  });
});

describe("answersPayload", () => {
  const qs = openState().questions;

  it("sends only changed answers, trimmed, and skips", () => {
    expect(
      answersPayload(qs, {
        q1: { answer: "  Legal ", skip: false },
        q2: { answer: "", skip: false },
        q3: { answer: "", skip: true },
      }),
    ).toEqual([
      { id: "q1", answer: "Legal" },
      { id: "q3", skip: true },
    ]);
  });

  it("is empty when nothing differs from the server", () => {
    const answered = answeredState().questions;
    expect(answersPayload(answered, {})).toEqual([]);
    expect(answersPayload(answered, { q1: { answer: "Legal", skip: false } })).toEqual([]);
    expect(answersPayload(answered, { q3: { answer: "", skip: true } })).toEqual([]);
    expect(answersPayload(answered, { q1: { answer: "Board", skip: false } })).toEqual([
      { id: "q1", answer: "Board" },
    ]);
  });
});

describe("answerProgress", () => {
  it("counts answered and skipped", () => {
    const p = answerProgress(openState().questions, {
      q1: { answer: "Legal", skip: false },
      q3: { answer: "", skip: true },
    });
    expect(p).toEqual({ answered: 1, skipped: 1, total: 3 });
    expect(progressLine(p)).toBe("1 of 3 answered · 1 skipped");
    expect(progressLine({ answered: 2, skipped: 0, total: 5 })).toBe("2 of 5 answered");
  });
});

describe("chips", () => {
  const opts = ["Word", "PDF", "Google Doc"];

  it("single-select replaces and toggles off", () => {
    expect(toggleOption("", "PDF", opts, false)).toBe("PDF");
    expect(toggleOption("Word", "PDF", opts, false)).toBe("PDF");
    expect(toggleOption("PDF", "PDF", opts, false)).toBe("");
    expect(selectedOptions("PDF", opts, false)).toEqual(["PDF"]);
    expect(selectedOptions("something else", opts, false)).toEqual([]);
  });

  it("multi-select toggles in the options' order", () => {
    expect(toggleOption("", "PDF", opts, true)).toBe("PDF");
    expect(toggleOption("PDF", "Word", opts, true)).toBe("Word; PDF");
    expect(toggleOption("Word; PDF", "Word", opts, true)).toBe("PDF");
    expect(selectedOptions("Word; Google Doc", opts, true)).toEqual(["Word", "Google Doc"]);
  });
});
