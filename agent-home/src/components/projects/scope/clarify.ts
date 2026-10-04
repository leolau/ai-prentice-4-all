import type {
  ClarifyAnswerInput,
  ClarifyCategory,
  ClarifyQuestion,
  ClarifyRound,
  ClarifyState,
  ClarifySummary,
} from "@/types";

/** How often the tab re-reads while the agent is writing its questions. */
export const CLARIFY_POLL_MS = 2_000;

/** Matches the BFF limits in `app/api/projects/[slug]/clarify/*`. */
export const FOCUS_MAX = 500;
export const ANSWER_MAX = 2000;
export const UNDERSTANDING_MAX = 4000;

/** Joins the chips of a multi-select answer. A semicolon, because options
 * often contain commas ("Partners, legal"). */
export const MULTI_SEPARATOR = "; ";

export const CATEGORY_LABEL: Record<ClarifyCategory, string> = {
  goal: "Goal",
  scope: "Scope",
  audience: "Audience",
  success: "Success",
  constraints: "Constraints",
  inputs: "Inputs",
  format: "Format",
  other: "Other",
};

export function clarifyPath(slug: string, sub?: "questions" | "answers" | "confirm"): string {
  const base = `/api/projects/${encodeURIComponent(slug)}/clarify`;
  return sub ? `${base}/${sub}` : base;
}

/** A state with no rounds, built from the detail read's summary so a fresh
 * project shows its hero before the full read lands. */
export function emptyClarifyState(summary?: ClarifySummary | null): ClarifyState {
  return {
    status: "not_started",
    round: 0,
    open_count: 0,
    answered_count: 0,
    understanding: null,
    confirmed_at: null,
    ...(summary ?? {}),
    rounds: [],
    questions: [],
    job: { status: "idle" },
  };
}

/** True when the payload looks like a `ClarifyState` (guards a bad proxy answer). */
export function isClarifyState(value: unknown): value is ClarifyState {
  if (!value || typeof value !== "object") return false;
  const v = value as Partial<ClarifyState>;
  return (
    typeof v.status === "string" &&
    Array.isArray(v.rounds) &&
    Array.isArray(v.questions) &&
    !!v.job &&
    typeof v.job === "object"
  );
}

export function latestRound(state: ClarifyState): ClarifyRound | null {
  if (state.rounds.length === 0) return null;
  return state.rounds.reduce((a, b) => (b.round_no > a.round_no ? b : a));
}

export function roundQuestions(state: ClarifyState, roundNo: number): ClarifyQuestion[] {
  return state.questions
    .filter((q) => q.round_no === roundNo)
    .sort((a, b) => a.position - b.position);
}

/** The latest *confirmed* understanding — the summary's, else the newest
 * confirmed round's. */
export function agreedUnderstanding(state: ClarifyState): string | null {
  if (state.understanding) return state.understanding;
  const confirmed = state.rounds
    .filter((r) => r.status === "confirmed" && r.understanding)
    .sort((a, b) => b.round_no - a.round_no);
  return confirmed[0]?.understanding ?? null;
}

/**
 * Which screen the tab shows. `answered` also covers a round where the agent
 * said it has enough and asked nothing (`done` with no questions).
 */
export type ScopeView = "not_started" | "open" | "answered" | "confirmed";

export function scopeView(state: ClarifyState): ScopeView {
  const round = latestRound(state);
  if (!round) return "not_started";
  if (round.status === "confirmed") return "confirmed";
  const open = roundQuestions(state, round.round_no).some((q) => q.status === "open");
  if (round.status === "open" && open) return "open";
  return "answered";
}

/** What the person has typed or picked for one question, before saving. */
export interface AnswerDraft {
  answer: string;
  skip: boolean;
}

export type AnswerDrafts = Record<string, AnswerDraft>;

export function serverDraft(q: ClarifyQuestion): AnswerDraft {
  return { answer: q.answer ?? "", skip: q.status === "skipped" };
}

export function effectiveDraft(q: ClarifyQuestion, drafts: AnswerDrafts): AnswerDraft {
  return drafts[q.id] ?? serverDraft(q);
}

/** A draft that would change what the server holds — and can be sent. */
function changedInput(q: ClarifyQuestion, draft: AnswerDraft): ClarifyAnswerInput | null {
  if (draft.skip) return q.status === "skipped" ? null : { id: q.id, skip: true };
  const answer = draft.answer.trim();
  // A blank answer cannot be sent (the server refuses it); leave the question as it is.
  if (!answer) return null;
  if (q.status === "answered" && (q.answer ?? "").trim() === answer) return null;
  return { id: q.id, answer };
}

/** Every changed answer of the round, in question order, for one POST. */
export function answersPayload(
  questions: ClarifyQuestion[],
  drafts: AnswerDrafts,
): ClarifyAnswerInput[] {
  return questions
    .map((q) => changedInput(q, effectiveDraft(q, drafts)))
    .filter((x): x is ClarifyAnswerInput => x !== null);
}

export interface AnswerProgress {
  answered: number;
  skipped: number;
  total: number;
}

export function answerProgress(questions: ClarifyQuestion[], drafts: AnswerDrafts): AnswerProgress {
  let answered = 0;
  let skipped = 0;
  for (const q of questions) {
    const d = effectiveDraft(q, drafts);
    if (d.skip) skipped++;
    else if (d.answer.trim()) answered++;
  }
  return { answered, skipped, total: questions.length };
}

export function progressLine({ answered, skipped, total }: AnswerProgress): string {
  const base = `${answered} of ${total} answered`;
  return skipped > 0 ? `${base} · ${skipped} skipped` : base;
}

/** The chips an answer currently selects. */
export function selectedOptions(answer: string, options: string[], allowMultiple: boolean): string[] {
  const text = answer.trim();
  if (!text) return [];
  if (!allowMultiple) return options.includes(text) ? [text] : [];
  const parts = text.split(";").map((p) => p.trim());
  return options.filter((o) => parts.includes(o.trim()));
}

/**
 * Tapping a suggested answer. Single-select: the chip becomes the answer
 * (tapping it again clears it). Multi-select: the chip is toggled in the
 * list, keeping the options' own order.
 */
export function toggleOption(
  answer: string,
  option: string,
  options: string[],
  allowMultiple: boolean,
): string {
  if (!allowMultiple) return answer.trim() === option ? "" : option;
  const current = selectedOptions(answer, options, true);
  const next = current.includes(option)
    ? current.filter((o) => o !== option)
    : options.filter((o) => o === option || current.includes(o));
  return next.join(MULTI_SEPARATOR);
}

export function formatWhen(epochSeconds: number | null | undefined): string | null {
  if (!epochSeconds) return null;
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}
