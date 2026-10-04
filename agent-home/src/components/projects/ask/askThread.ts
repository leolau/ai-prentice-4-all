/**
 * Pure logic for the ask-or-change box: the session-local Q&A thread
 * (capped, persisted per project in `sessionStorage`), where each cited
 * source links to, and the change-mode kind prefix.
 */
import type { ProjectTab } from "@/components/projects/tabs/types";
import type {
  AskProjectHistoryTurn,
  AskProjectSource,
  AskProjectSourceKind,
} from "@/types";

export type AskTurnStatus = "pending" | "done" | "error";

export interface AskTurn {
  id: string;
  q: string;
  status: AskTurnStatus;
  a?: string;
  sources?: AskProjectSource[];
  suggestion?: string;
  error?: string;
}

/** Turns kept in the thread (and in storage). */
export const MAX_TURNS = 10;
/** Answered turns sent back as context — the server accepts at most 6. */
export const HISTORY_TURNS = 6;
export const QUESTION_MAX = 2000;

export const ASK_SUGGESTIONS: ReadonlyArray<{ label: string; question: string }> = [
  { label: "What's left?", question: "What's left before this project is done?" },
  {
    label: "What changed since the last run?",
    question: "What changed since the last run?",
  },
  { label: "Why is it slow?", question: "Why is it slow? What is it waiting on?" },
];

export function capTurns(turns: AskTurn[], max = MAX_TURNS): AskTurn[] {
  return turns.length > max ? turns.slice(turns.length - max) : turns;
}

/** The answered turns to send as context, oldest first. */
export function historyFor(turns: AskTurn[], max = HISTORY_TURNS): AskProjectHistoryTurn[] {
  return turns
    .filter((t) => t.status === "done" && t.a)
    .slice(-max)
    .map((t) => ({ q: t.q, a: t.a as string }));
}

export function updateTurn(
  turns: AskTurn[],
  id: string,
  patch: Partial<AskTurn>,
): AskTurn[] {
  return turns.map((t) => (t.id === id ? { ...t, ...patch } : t));
}

export function storageKey(slug: string): string {
  return `projects.ask.${slug}`;
}

const KINDS: ReadonlySet<string> = new Set<AskProjectSourceKind>([
  "card",
  "run",
  "output",
  "requirement",
  "plan",
  "event",
]);

function isSource(value: unknown): value is AskProjectSource {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.kind === "string" &&
    KINDS.has(v.kind) &&
    typeof v.id === "string" &&
    typeof v.label === "string"
  );
}

function asTurn(value: unknown): AskTurn | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  if (typeof v.id !== "string" || typeof v.q !== "string") return null;
  if (v.status === "done" && typeof v.a === "string") {
    return {
      id: v.id,
      q: v.q,
      status: "done",
      a: v.a,
      sources: Array.isArray(v.sources) ? v.sources.filter(isSource) : [],
      ...(typeof v.suggestion === "string" ? { suggestion: v.suggestion } : {}),
    };
  }
  if (v.status === "error") {
    return {
      id: v.id,
      q: v.q,
      status: "error",
      error: typeof v.error === "string" ? v.error : "That did not go through.",
    };
  }
  // A question still in flight when the page went away has no answer to
  // restore; it is dropped rather than shown spinning forever.
  return null;
}

type ReadStorage = Pick<Storage, "getItem"> | null | undefined;
type WriteStorage = Pick<Storage, "setItem" | "removeItem"> | null | undefined;

/** The stored thread as raw text (stable for `useSyncExternalStore`). */
export function readThreadRaw(storage: ReadStorage, slug: string): string | null {
  if (!storage) return null;
  try {
    return storage.getItem(storageKey(slug));
  } catch {
    return null;
  }
}

export function parseThread(raw: string | null): AskTurn[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return capTurns(parsed.map(asTurn).filter((t): t is AskTurn => t !== null));
  } catch {
    return [];
  }
}

export function loadThread(storage: ReadStorage, slug: string): AskTurn[] {
  return parseThread(readThreadRaw(storage, slug));
}

export function saveThread(storage: WriteStorage, slug: string, turns: AskTurn[]): void {
  if (!storage) return;
  try {
    const keep = capTurns(turns.filter((t) => t.status !== "pending"));
    if (keep.length === 0) storage.removeItem(storageKey(slug));
    else storage.setItem(storageKey(slug), JSON.stringify(keep));
  } catch {
    // Storage full or disabled — the thread just won't survive a refresh.
  }
}

/** The project tab a source lives on, when it has no page of its own. */
export function sourceTab(source: AskProjectSource): ProjectTab | null {
  switch (source.kind) {
    case "output":
      return "outputs";
    case "requirement":
      return "iterations";
    case "plan":
      return "plan";
    case "event":
      return "board";
    default:
      return null;
  }
}

export function sourceHref(slug: string, source: AskProjectSource): string {
  const base = `/projects/${encodeURIComponent(slug)}`;
  if (source.kind === "card") return `${base}/cards/${encodeURIComponent(source.id)}`;
  if (source.kind === "run") return `${base}/runs/${encodeURIComponent(source.id)}`;
  const tab = sourceTab(source);
  return tab ? `${base}?tab=${tab}` : base;
}

/** What "Turn into a requirement…" pre-fills. */
export function suggestedRequirement(turn: AskTurn): string {
  return (turn.suggestion ?? "").trim() || turn.q;
}

export type ChangeKind = "requirement" | "direction" | "output" | "file" | "memory";

export const CHANGE_KINDS: ReadonlyArray<{ id: ChangeKind; label: string; icon: string }> = [
  { id: "requirement", label: "Add requirement", icon: "＋" },
  { id: "direction", label: "Change direction", icon: "↪" },
  { id: "output", label: "Change an output", icon: "✎" },
  { id: "file", label: "Attach file", icon: "📎" },
  { id: "memory", label: "Add memory", icon: "🧠" },
];

export function withKindPrefix(kind: ChangeKind | null, text: string): string {
  const body = text.trim();
  const label = CHANGE_KINDS.find((k) => k.id === kind)?.label;
  if (!label) return body;
  return body ? `${label}: ${body}` : `${label}: `;
}

/** Enter sends; Shift+Enter is a newline; an IME composition never sends. */
export function isSendKey(e: {
  key: string;
  shiftKey: boolean;
  isComposing?: boolean;
  keyCode?: number;
}): boolean {
  if (e.key !== "Enter" || e.shiftKey) return false;
  if (e.isComposing || e.keyCode === 229) return false;
  return true;
}

let seq = 0;
export function newTurnId(): string {
  seq += 1;
  return `t_${Date.now().toString(36)}_${seq}`;
}
