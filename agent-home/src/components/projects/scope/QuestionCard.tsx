"use client";

import {
  ANSWER_MAX,
  CATEGORY_LABEL,
  selectedOptions,
  toggleOption,
  type AnswerDraft,
} from "@/components/projects/scope/clarify";
import { CARD, CHIP_BASE, CHIP_OFF, CHIP_ON, INPUT, LINK } from "@/components/projects/scope/ui";
import type { ClarifyQuestion } from "@/types";

/** One of the agent's questions: why it asks, suggested answers, a free answer, Skip. */
export function QuestionCard({
  question,
  draft,
  onChange,
  readOnly = false,
  disabled = false,
}: {
  question: ClarifyQuestion;
  draft: AnswerDraft;
  onChange: (draft: AnswerDraft) => void;
  readOnly?: boolean;
  disabled?: boolean;
}) {
  const selected = selectedOptions(draft.answer, question.options, question.allow_multiple);
  const inputId = `clarify-answer-${question.id}`;
  return (
    <li
      data-component="ClarifyQuestion"
      data-question-id={question.id}
      data-state={draft.skip ? "skipped" : draft.answer.trim() ? "answered" : "open"}
      className={CARD}
    >
      <span
        data-component="ClarifyCategory"
        className="rounded-full bg-[var(--color-surface-2)] px-2 py-0.5 text-xs text-[var(--color-muted)]"
      >
        {CATEGORY_LABEL[question.category] ?? CATEGORY_LABEL.other}
      </span>
      <h3 className="mt-2 text-sm font-semibold">{question.question}</h3>
      {question.why ? (
        <p className="mt-1 text-xs text-[var(--color-muted)]">{question.why}</p>
      ) : null}
      {readOnly ? (
        <ReadOnlyAnswer draft={draft} />
      ) : (
        <>
          {question.options.length > 0 ? (
            <div
              role="group"
              aria-label={question.allow_multiple ? "Suggested answers (pick any)" : "Suggested answers"}
              className="mt-3 flex flex-wrap gap-2"
            >
              {question.options.map((option) => {
                const on = !draft.skip && selected.includes(option);
                return (
                  <button
                    key={option}
                    type="button"
                    aria-pressed={on}
                    disabled={disabled}
                    className={`${CHIP_BASE} ${on ? CHIP_ON : CHIP_OFF}`}
                    onClick={() =>
                      onChange({
                        skip: false,
                        answer: toggleOption(
                          draft.skip ? "" : draft.answer,
                          option,
                          question.options,
                          question.allow_multiple,
                        ),
                      })
                    }
                  >
                    {option}
                  </button>
                );
              })}
            </div>
          ) : null}
          {draft.skip ? (
            <p className="mt-3 flex items-center gap-2 text-sm text-[var(--color-muted)]">
              Skipped.
              <button
                type="button"
                className={LINK}
                disabled={disabled}
                onClick={() => onChange({ ...draft, skip: false })}
              >
                Answer instead
              </button>
            </p>
          ) : (
            <>
              <label htmlFor={inputId} className="sr-only">
                Your answer
              </label>
              <textarea
                id={inputId}
                rows={2}
                maxLength={ANSWER_MAX}
                value={draft.answer}
                disabled={disabled}
                placeholder={question.options.length > 0 ? "Pick above or type your own" : "Your answer"}
                className={`mt-3 ${INPUT}`}
                onChange={(e) => onChange({ skip: false, answer: e.target.value })}
              />
              <div className="mt-2 flex justify-end">
                <button
                  type="button"
                  className="text-xs text-[var(--color-muted)] underline-offset-2 hover:underline disabled:opacity-60"
                  disabled={disabled}
                  onClick={() => onChange({ ...draft, skip: true })}
                >
                  Skip
                </button>
              </div>
            </>
          )}
        </>
      )}
    </li>
  );
}

function ReadOnlyAnswer({ draft }: { draft: AnswerDraft }) {
  if (draft.skip) return <p className="mt-3 text-sm text-[var(--color-muted)]">Skipped</p>;
  if (!draft.answer.trim()) {
    return <p className="mt-3 text-sm italic text-[var(--color-muted)]">Not answered yet</p>;
  }
  return <p className="mt-3 whitespace-pre-wrap text-sm">{draft.answer}</p>;
}
