"use client";

import { ActionButton } from "@/components/projects/ActionButton";
import { ActionError } from "@/components/projects/ActionError";
import { UNDERSTANDING_MAX } from "@/components/projects/scope/clarify";
import { FocusInput } from "@/components/projects/scope/FocusInput";
import { CARD, INPUT, PRIMARY, SECONDARY } from "@/components/projects/scope/ui";
import type { ScopeActions } from "@/components/projects/scope/useScopeActions";
import type { ClarifyRound } from "@/types";

/**
 * "Here's what I understood" — the agent's summary of goal and scope, which
 * the owner can edit, then confirm (optionally drafting the plan from it) or
 * send the agent back for a follow-up round.
 */
export function UnderstandingCard({
  round,
  text,
  onText,
  focus,
  onFocus,
  actions,
  readOnly,
  confirmBlocked = null,
  onConfirm,
}: {
  round: ClarifyRound;
  text: string;
  onText: (text: string) => void;
  focus: string;
  onFocus: (focus: string) => void;
  actions: ScopeActions;
  readOnly: boolean;
  /** Why confirming has to wait (e.g. unsaved answers), or null. */
  confirmBlocked?: string | null;
  onConfirm: (draftPlan: boolean) => void;
}) {
  const disabled = actions.anyBusy;
  return (
    <section data-component="ClarifyUnderstanding" className={CARD}>
      <h2 className="text-base font-semibold">Here&rsquo;s what I understood</h2>
      {round.done ? (
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          The agent has what it needs to plan. Correct anything that&rsquo;s off, then confirm.
        </p>
      ) : (
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          Correct anything that&rsquo;s off. The agent may still have a few follow-up questions.
        </p>
      )}
      {readOnly ? (
        <p className="mt-3 whitespace-pre-wrap text-sm">
          {text.trim() || "The agent hasn't summarised the scope yet."}
        </p>
      ) : (
        <>
          <label htmlFor="clarify-understanding" className="sr-only">
            The agent&rsquo;s understanding
          </label>
          <textarea
            id="clarify-understanding"
            rows={6}
            maxLength={UNDERSTANDING_MAX}
            value={text}
            disabled={disabled}
            placeholder="Goal, scope, audience and what done looks like"
            className={`mt-3 ${INPUT}`}
            onChange={(e) => onText(e.target.value)}
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <ActionButton
              busy={actions.confirmDraft.busy}
              disabled={disabled || !!confirmBlocked}
              pendingLabel="Confirming…"
              className={PRIMARY}
              onClick={() => onConfirm(true)}
            >
              Confirm &amp; draft the plan
            </ActionButton>
            <ActionButton
              busy={actions.confirm.busy}
              disabled={disabled || !!confirmBlocked}
              pendingLabel="Confirming…"
              className={SECONDARY}
              onClick={() => onConfirm(false)}
            >
              Confirm only
            </ActionButton>
          </div>
          {confirmBlocked ? (
            <p className="mt-2 text-xs text-[var(--color-warn-text)]">{confirmBlocked}</p>
          ) : null}
          <ActionError action={actions.confirmDraft} />
          <ActionError action={actions.confirm} />
          <div className="mt-4 border-t border-[var(--color-border)] pt-3">
            <FocusInput
              id="clarify-followup-focus"
              label="Anything the follow-up should dig into?"
              value={focus}
              onChange={onFocus}
              disabled={disabled}
            />
            <ActionButton
              busy={actions.ask.busy}
              disabled={disabled}
              pendingLabel="Asking…"
              className={`mt-2 ${SECONDARY}`}
              onClick={() => actions.askQuestions(focus)}
            >
              Ask follow-up questions
            </ActionButton>
            <ActionError action={actions.ask} />
          </div>
        </>
      )}
    </section>
  );
}
