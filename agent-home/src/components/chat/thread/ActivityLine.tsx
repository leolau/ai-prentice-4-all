"use client";

import { memo } from "react";

import type { ToolChip } from "@/components/chat/LiveActivity";
import { StatusIndicator, type ChatActivity } from "@/components/chat/StatusIndicator";

/**
 * The single inline activity line of an in-flight turn: a compact status
 * (phase + self-ticking clock) and a collapsed "N steps" disclosure listing
 * the tool calls and the live reasoning text. Muted — activity, not reply.
 */
export const ActivityLine = memo(function ActivityLine({
  activity,
  tools,
  reasoning,
  startedAt,
  lastEventAt,
  hasOutput,
}: {
  activity: ChatActivity;
  tools: ToolChip[];
  reasoning: string;
  startedAt?: number;
  lastEventAt?: number;
  hasOutput: boolean;
}) {
  const hasSteps = tools.length > 0 || reasoning !== "";
  if (activity === "idle" && !hasSteps) return null;
  const running = tools.find((t) => !t.done);
  const summary =
    tools.length > 0
      ? `${tools.length} ${tools.length === 1 ? "step" : "steps"}`
      : "Reasoning";
  return (
    <div data-component="ActivityLine" className="flex flex-col gap-1">
      <StatusIndicator
        compact
        activity={activity}
        detail={running?.name}
        startedAt={startedAt}
        lastEventAt={lastEventAt}
        hasOutput={hasOutput}
      />
      {hasSteps ? (
        <details
          data-component="LiveActivity"
          className="text-xs text-[var(--color-muted)]"
        >
          <summary className="cursor-pointer select-none">{summary}</summary>
          <div className="mt-1 space-y-1">
            {tools.map((t, i) => (
              <div key={t.id || `${t.name}-${i}`}>
                {t.done ? `${t.name} — done` : `${t.name} — running…`}
              </div>
            ))}
            {reasoning !== "" ? (
              <div
                data-component="LiveReasoning"
                className="max-h-24 overflow-y-auto whitespace-pre-wrap rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 italic"
              >
                {reasoning}
              </div>
            ) : null}
          </div>
        </details>
      ) : null}
    </div>
  );
});
