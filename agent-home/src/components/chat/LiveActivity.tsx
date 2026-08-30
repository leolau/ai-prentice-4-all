import type { ChatToolEvent } from "@/lib/chat/stream";

export interface ToolChip extends ChatToolEvent {
  done: boolean;
}

/**
 * Live "what is the agent doing" surface for a streaming turn: the model's
 * reasoning text as it arrives (so a long think never looks frozen) plus one
 * line per tool call. Muted on purpose — it is activity, not conversation.
 */
export function LiveActivity({
  reasoning,
  tools,
}: {
  reasoning: string;
  tools: ToolChip[];
}) {
  if (reasoning === "" && tools.length === 0) return null;
  return (
    <div data-component="LiveActivity" className="mt-2 space-y-1">
      {tools.map((t, i) => (
        <div
          key={t.id || `${t.name}-${i}`}
          className="text-xs text-[var(--color-muted)]"
        >
          {t.done ? `${t.name} — done` : `${t.name} — running…`}
        </div>
      ))}
      {reasoning !== "" ? (
        <div
          data-component="LiveReasoning"
          className="max-h-24 overflow-y-auto whitespace-pre-wrap rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-xs italic text-[var(--color-muted)]"
        >
          {reasoning}
        </div>
      ) : null}
    </div>
  );
}
