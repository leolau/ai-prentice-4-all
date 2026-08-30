/**
 * Display-side sanitization of persisted one-brain transcripts.
 *
 * The Python session store returns every row verbatim, including internal
 * scaffolding that must never render as a chat bubble: context-compaction
 * summaries and the machine-injected `[app context: …]` line prepended to
 * user turns by `ui-context.ts`. Stripping happens ONLY at render time —
 * search and the model's own history keep the raw rows.
 *
 * Keep the markers below in sync with `agent/context_compressor.py`:
 * `SUMMARY_PREFIX` (~L44), `LEGACY_SUMMARY_PREFIX` (~L71),
 * `_SUMMARY_END_MARKER` (~L93) and `_MERGED_PRIOR_CONTEXT_HEADER` /
 * `_MERGED_SUMMARY_DELIMITER` (~L103).
 */
import type { ChatMessage } from "@/types";

export const COMPACTION_PREFIXES = [
  "[CONTEXT COMPACTION — REFERENCE ONLY]",
  "[CONTEXT COMPACTION - REFERENCE ONLY]",
  "[CONTEXT SUMMARY]:",
] as const;

export const COMPACTION_END_MARKER =
  "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---";

/** Prepended to a tail message when the summary is merged into it. */
export const MERGED_PRIOR_CONTEXT_HEADER =
  "[PRIOR CONTEXT — for reference only; not a new message]";

/** Separates the preserved original content from the merged summary. */
export const MERGED_SUMMARY_DELIMITER =
  "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]";

export interface CompactionSplit {
  /** The user-visible remainder ("" when the row is summary-only). */
  display: string;
  /** True when the row contained compaction material. */
  hadCompaction: boolean;
}

/**
 * Split a persisted message into what may be shown to the user.
 *
 * Three shapes exist:
 * 1. Standalone summary — `<prefix>…<body>\n\n<END marker>` → display "".
 * 2. Legacy merge — `<prefix>…<body>\n\n<END marker>\n<original reply>` →
 *    display = the original reply after the marker.
 * 3. Current merge — `<HEADER>\n<original content>\n\n<DELIMITER>\n\n<summary>
 *    \n\n<END marker>` → display = the original content between header and
 *    delimiter.
 */
export function splitCompactionContent(content: string): CompactionSplit {
  const head = content.trimStart();

  if (head.startsWith(MERGED_PRIOR_CONTEXT_HEADER)) {
    const headerIdx = content.indexOf(MERGED_PRIOR_CONTEXT_HEADER);
    const delimIdx = content.indexOf(MERGED_SUMMARY_DELIMITER);
    if (delimIdx < 0) return { display: "", hadCompaction: true };
    const display = content
      .slice(headerIdx + MERGED_PRIOR_CONTEXT_HEADER.length, delimIdx)
      .trim();
    return { display, hadCompaction: true };
  }

  if (COMPACTION_PREFIXES.some((p) => head.startsWith(p))) {
    const markerIdx = content.indexOf(COMPACTION_END_MARKER);
    if (markerIdx < 0) return { display: "", hadCompaction: true };
    const display = content
      .slice(markerIdx + COMPACTION_END_MARKER.length)
      .replace(/^\s+/, "");
    return { display, hadCompaction: true };
  }

  return { display: content, hadCompaction: false };
}

/** Remove the single `[app context: …]` line `withUiContext()` prepends. */
export function stripUiContextLine(content: string): string {
  return content.replace(/^\[app context:[^\n]*\][ \t]*\r?\n?/, "");
}

/** Rows a user-facing transcript renders (mirrors ChatPane's filter). */
export function visibleTurns(messages: ChatMessage[]): ChatMessage[] {
  return messages.filter((m) => m.role === "user" || m.role === "assistant");
}
