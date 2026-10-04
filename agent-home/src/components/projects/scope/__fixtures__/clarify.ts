import type { ClarifyQuestion, ClarifyRound, ClarifyState } from "@/types";

/** Shared Scope-tab test builders. */
export const T0 = 1_760_000_000;

export function question(over: Partial<ClarifyQuestion> = {}): ClarifyQuestion {
  return {
    id: "q1",
    round_no: 1,
    position: 0,
    category: "audience",
    question: "Who reads the MOUs?",
    why: "Sets the tone and level of detail.",
    options: ["Partners", "Legal", "Board"],
    allow_multiple: false,
    status: "open",
    answer: null,
    answered_by: null,
    answered_at: null,
    created_at: T0,
    ...over,
  };
}

export function round(over: Partial<ClarifyRound> = {}): ClarifyRound {
  return {
    round_no: 1,
    status: "open",
    understanding: "Four MOUs, one per partner, for legal sign-off.",
    done: false,
    focus: null,
    created_by: "yan",
    created_at: T0,
    confirmed_by: null,
    confirmed_at: null,
    ...over,
  };
}

export function clarifyState(over: Partial<ClarifyState> = {}): ClarifyState {
  return {
    status: "not_started",
    round: 0,
    open_count: 0,
    answered_count: 0,
    understanding: null,
    confirmed_at: null,
    rounds: [],
    questions: [],
    job: { status: "idle" },
    ...over,
  };
}

/** Round 1 open with three questions (one multi-select, one without options). */
export function openState(over: Partial<ClarifyState> = {}): ClarifyState {
  return clarifyState({
    status: "open",
    round: 1,
    open_count: 3,
    rounds: [round()],
    questions: [
      question(),
      question({
        id: "q2",
        position: 1,
        category: "format",
        question: "Which formats do you need?",
        why: "Decides the deliverables.",
        options: ["Word", "PDF", "Google Doc"],
        allow_multiple: true,
      }),
      question({
        id: "q3",
        position: 2,
        category: "constraints",
        question: "Any deadline?",
        why: null,
        options: [],
      }),
    ],
    ...over,
  });
}

/** Round 1 answered (q1, q2) and skipped (q3), not yet confirmed. */
export function answeredState(over: Partial<ClarifyState> = {}): ClarifyState {
  const base = openState();
  return clarifyState({
    ...base,
    status: "answered",
    open_count: 0,
    answered_count: 2,
    rounds: [round({ status: "answered", done: true })],
    questions: base.questions.map((q) =>
      q.id === "q3"
        ? { ...q, status: "skipped" }
        : { ...q, status: "answered", answer: q.id === "q1" ? "Legal" : "Word; PDF", answered_by: "yan" },
    ),
    ...over,
  });
}

export function confirmedState(over: Partial<ClarifyState> = {}): ClarifyState {
  const base = answeredState();
  return clarifyState({
    ...base,
    status: "confirmed",
    understanding: "Four MOUs for legal, in Word and PDF.",
    confirmed_at: T0 + 60,
    rounds: [
      round({
        status: "confirmed",
        done: true,
        understanding: "Four MOUs for legal, in Word and PDF.",
        confirmed_by: "yan",
        confirmed_at: T0 + 60,
      }),
    ],
    ...over,
  });
}
