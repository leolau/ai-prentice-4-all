import { Spinner } from "@/components/ui/Spinner";

/**
 * Animated agent-activity indicator for the chat pane.
 *
 * Gives the user a live signal that the a4all agent is actually working, since
 * a streamed turn can otherwise look frozen between tokens. Phases:
 *   - `sending`   — the message left the browser, no server ack yet;
 *   - `thinking`  — turn accepted, no text yet (the model is composing);
 *   - `tool`      — a tool call is running (`detail` names it);
 *   - `streaming` — tokens are arriving (the reply is being written);
 *   - `waiting_approval` — a gated tool is blocked on the user's decision.
 * Every active phase shows the elapsed time so a long turn visibly advances.
 * Rendered as an aria-live region so the state is also announced to AT.
 */
export type ChatActivity =
  | "idle"
  | "sending"
  | "thinking"
  | "tool"
  | "streaming"
  | "waiting_approval";

/** Silence (no assistant text yet) after which we explain the wait. */
export const LONG_TASK_HINT_MS = 20_000;
/** No stream event of any kind for this long reads as a possible stall. */
export const STALL_WARNING_MS = 90_000;

const LABELS: Record<Exclude<ChatActivity, "idle" | "tool">, string> = {
  sending: "Received · connecting…",
  thinking: "a4all agent is thinking…",
  streaming: "a4all agent is writing…",
  waiting_approval: "Waiting for your approval…",
};

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function Dots() {
  return (
    <span className="inline-flex items-center gap-1" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-current"
          style={{ animationDelay: `${i * 150}ms`, animationDuration: "1s" }}
        />
      ))}
    </span>
  );
}

export function StatusIndicator({
  activity,
  elapsedMs,
  detail,
  quietMs,
  hasOutput = false,
}: {
  activity: ChatActivity;
  /** Time since the user sent the message; omitted → no clock. */
  elapsedMs?: number;
  /** Phase detail, e.g. the running tool's name. */
  detail?: string;
  /** Time since the last stream event of any kind (for stall detection). */
  quietMs?: number;
  /** Whether any assistant text has arrived yet. */
  hasOutput?: boolean;
}) {
  if (activity === "idle") return null;
  const waiting = activity === "waiting_approval";
  const label =
    activity === "tool" ? `Running ${detail || "a tool"}…` : LABELS[activity];
  const clock =
    elapsedMs !== undefined && elapsedMs >= 1000 ? formatElapsed(elapsedMs) : null;
  const stalled = !waiting && (quietMs ?? 0) >= STALL_WARNING_MS;
  const longTask =
    !waiting && !stalled && !hasOutput && (elapsedMs ?? 0) >= LONG_TASK_HINT_MS;
  return (
    <div
      data-component="StatusIndicator"
      data-activity={activity}
      role="status"
      aria-live="polite"
      className="flex flex-col items-start gap-1"
    >
      <span
        className={`inline-flex items-center gap-2 rounded-2xl border border-[var(--color-accent)] bg-[var(--color-surface-2)] px-3 py-2 text-sm font-medium text-[var(--color-accent)] ${
          waiting ? "animate-pulse" : ""
        }`}
      >
        {waiting ? <Dots /> : <Spinner />}
        <span>{label}</span>
        {clock ? (
          <span
            data-component="StatusElapsed"
            className="tabular-nums text-xs font-normal opacity-70"
          >
            {clock}
          </span>
        ) : null}
      </span>
      {stalled ? (
        <p
          data-component="StatusStallWarning"
          className="max-w-prose px-1 text-xs text-amber-300"
        >
          No activity for {formatElapsed(quietMs ?? 0)}. The agent may be waiting
          on a slow step — you can Stop and try again if it looks stuck.
        </p>
      ) : longTask ? (
        <p
          data-component="StatusLongTaskHint"
          className="max-w-prose px-1 text-xs text-[var(--color-muted)]"
        >
          This is taking a while. You can leave this page — the task keeps
          running on the server and the reply will be here when you come back.
        </p>
      ) : null}
    </div>
  );
}
