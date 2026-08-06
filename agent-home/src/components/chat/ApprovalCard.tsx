"use client";

import type { ChatApprovalRequest } from "@/types";

/** Human labels for the approval choices the agent offers, in display order. */
const CHOICE_LABELS: Record<string, string> = {
  once: "Approve",
  session: "Approve for this chat",
  always: "Always approve",
  deny: "Deny",
};

const CHOICE_ORDER = ["once", "session", "always", "deny"];

export interface ApprovalCardProps {
  request: ChatApprovalRequest;
  busy: boolean;
  onResolve(choice: string): void;
}

/**
 * Inline approve/deny card shown when a tool-approval-gated tool blocks the
 * turn (`approval.request`). The agent is paused until the user picks a choice,
 * which resolves through the BFF (`POST /api/chat/approval`). This is the
 * approval surface agent-home chat previously lacked — without it gated tools
 * failed closed with `no_surface`.
 */
export function ApprovalCard({ request, busy, onResolve }: ApprovalCardProps) {
  const choices = CHOICE_ORDER.filter((c) => request.choices.includes(c));
  const label =
    request.command || request.toolName || request.patternKey || "a tool";
  return (
    <div
      data-component="ApprovalCard"
      className="mt-2 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface-2)] p-3 text-sm"
    >
      <p className="font-semibold text-[var(--color-fg)]">Approval needed</p>
      <p className="mt-1 text-[var(--color-muted)]">
        {request.description ||
          "Your agent needs approval before running this tool."}
      </p>
      <p className="mt-1 break-words font-mono text-xs text-[var(--color-fg)]">
        {label}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {choices.map((choice) => {
          const isDeny = choice === "deny";
          return (
            <button
              key={choice}
              type="button"
              disabled={busy}
              onClick={() => onResolve(choice)}
              className={`rounded-xl px-3 py-2 text-sm font-semibold disabled:opacity-50 ${
                isDeny
                  ? "border border-[var(--color-border)] text-red-300"
                  : "bg-[var(--color-accent)] text-[var(--color-accent-fg)]"
              }`}
            >
              {CHOICE_LABELS[choice] ?? choice}
            </button>
          );
        })}
      </div>
    </div>
  );
}
