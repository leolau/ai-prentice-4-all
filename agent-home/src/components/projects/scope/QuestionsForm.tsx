"use client";

import { ActionButton } from "@/components/projects/ActionButton";
import { ActionError } from "@/components/projects/ActionError";
import {
  answerProgress,
  answersPayload,
  effectiveDraft,
  progressLine,
  type AnswerDrafts,
} from "@/components/projects/scope/clarify";
import { QuestionCard } from "@/components/projects/scope/QuestionCard";
import { LINK, PRIMARY } from "@/components/projects/scope/ui";
import type { ScopeActions } from "@/components/projects/scope/useScopeActions";
import type { ClarifyQuestion } from "@/types";

/**
 * A round's questions with one "Save answers" that posts every changed
 * answer at once. Answers stay editable until the round is confirmed.
 */
export function QuestionsForm({
  questions,
  drafts,
  setDrafts,
  actions,
  readOnly,
}: {
  questions: ClarifyQuestion[];
  drafts: AnswerDrafts;
  setDrafts: (update: (prev: AnswerDrafts) => AnswerDrafts) => void;
  actions: ScopeActions;
  readOnly: boolean;
}) {
  const payload = answersPayload(questions, drafts);
  const progress = answerProgress(questions, drafts);
  const unanswered = questions.filter((q) => {
    const d = effectiveDraft(q, drafts);
    return !d.skip && !d.answer.trim();
  });
  const disabled = actions.anyBusy;
  return (
    <div data-component="ClarifyQuestions" className="flex flex-col gap-3">
      <p data-component="ClarifyProgress" className="text-sm text-[var(--color-muted)]">
        {progressLine(progress)}
      </p>
      <ol className="flex flex-col gap-3">
        {questions.map((q) => (
          <QuestionCard
            key={q.id}
            question={q}
            draft={effectiveDraft(q, drafts)}
            readOnly={readOnly}
            disabled={disabled}
            onChange={(draft) => setDrafts((prev) => ({ ...prev, [q.id]: draft }))}
          />
        ))}
      </ol>
      {readOnly ? null : (
        <div className="flex flex-wrap items-center gap-3">
          <ActionButton
            busy={actions.save.busy}
            disabled={disabled || payload.length === 0}
            pendingLabel="Saving…"
            className={PRIMARY}
            onClick={() => actions.saveAnswers(payload, () => setDrafts(() => ({})))}
          >
            Save answers
          </ActionButton>
          {unanswered.length > 0 ? (
            <button
              type="button"
              className={LINK}
              disabled={disabled}
              onClick={() =>
                setDrafts((prev) => {
                  const next = { ...prev };
                  for (const q of unanswered) next[q.id] = { answer: "", skip: true };
                  return next;
                })
              }
            >
              Skip the rest
            </button>
          ) : null}
          {payload.length > 0 && !actions.save.busy ? (
            <span className="text-xs text-[var(--color-muted)]">Unsaved changes</span>
          ) : null}
        </div>
      )}
      <ActionError action={actions.save} />
    </div>
  );
}
