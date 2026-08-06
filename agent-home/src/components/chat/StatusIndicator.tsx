/**
 * Animated agent-activity indicator for the chat pane.
 *
 * Gives the user a live signal that the a4all agent is actually working, since
 * a streamed turn can otherwise look frozen between tokens. Three phases:
 *   - `thinking`  — turn accepted, no text yet (the model is composing);
 *   - `streaming` — tokens are arriving (the reply is being written);
 *   - `waiting_approval` — a gated tool is blocked on the user's decision.
 * Rendered as an aria-live region so the state is also announced to AT.
 */
export type ChatActivity =
  | "idle"
  | "thinking"
  | "streaming"
  | "waiting_approval";

const LABELS: Record<Exclude<ChatActivity, "idle">, string> = {
  thinking: "a4all agent is thinking…",
  streaming: "a4all agent is responding…",
  waiting_approval: "Waiting for your approval…",
};

function Dots({ className }: { className: string }) {
  return (
    <span className="inline-flex items-center gap-1" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className={`h-1.5 w-1.5 animate-bounce rounded-full ${className}`}
          style={{ animationDelay: `${i * 150}ms`, animationDuration: "1s" }}
        />
      ))}
    </span>
  );
}

export function StatusIndicator({ activity }: { activity: ChatActivity }) {
  if (activity === "idle") return null;
  const waiting = activity === "waiting_approval";
  return (
    <div
      data-component="StatusIndicator"
      data-activity={activity}
      role="status"
      aria-live="polite"
      className="flex justify-start"
    >
      <span
        className={`inline-flex items-center gap-2 rounded-2xl px-3 py-2 text-sm ${
          waiting
            ? "animate-pulse bg-[var(--color-surface-2)] text-[var(--color-accent)]"
            : "bg-[var(--color-surface-2)] text-[var(--color-muted)]"
        }`}
      >
        <Dots
          className={waiting ? "bg-[var(--color-accent)]" : "bg-[var(--color-muted)]"}
        />
        {LABELS[activity]}
      </span>
    </div>
  );
}
