"use client";

import { useCallback } from "react";

import { clarifyPath } from "@/components/projects/scope/clarify";
import { useProjectAction, type ProjectAction } from "@/components/projects/useProjectAction";
import type { ClarifyAnswerInput, ClarifyConfirmResult, ClarifyState } from "@/types";

export interface ScopeActions {
  ask: ProjectAction<ClarifyState>;
  save: ProjectAction<ClarifyState>;
  confirm: ProjectAction<ClarifyConfirmResult>;
  confirmDraft: ProjectAction<ClarifyConfirmResult>;
  /** Any Scope write in flight — every Scope button waits for it. */
  anyBusy: boolean;
  askQuestions: (focus: string) => void;
  saveAnswers: (answers: ClarifyAnswerInput[], onSaved: () => void) => void;
  confirmScope: (
    understanding: string | undefined,
    draftPlan: boolean,
    onDone: (result: ClarifyConfirmResult) => void,
  ) => void;
}

/**
 * The Scope tab's writes, one `useProjectAction` each (so a failure's Retry
 * re-sends that write's own key). Every answer carries a fresh
 * `ClarifyState`, applied before the page refresh starts.
 */
export function useScopeActions(slug: string, apply: (state: ClarifyState) => void): ScopeActions {
  const ask = useProjectAction<ClarifyState>();
  const save = useProjectAction<ClarifyState>();
  const confirm = useProjectAction<ClarifyConfirmResult>();
  const confirmDraft = useProjectAction<ClarifyConfirmResult>();
  const anyBusy = ask.busy || save.busy || confirm.busy || confirmDraft.busy;

  const askRun = ask.run;
  const askQuestions = useCallback(
    (focus: string) => {
      const trimmed = focus.trim();
      void askRun(clarifyPath(slug, "questions"), {
        body: trimmed ? { focus: trimmed } : {},
        onSuccess: apply,
      });
    },
    [apply, askRun, slug],
  );

  const saveRun = save.run;
  const saveAnswers = useCallback(
    (answers: ClarifyAnswerInput[], onSaved: () => void) => {
      if (answers.length === 0) return;
      void saveRun(clarifyPath(slug, "answers"), {
        body: { answers },
        onSuccess: (state) => {
          apply(state);
          onSaved();
        },
      });
    },
    [apply, saveRun, slug],
  );

  const confirmRun = confirm.run;
  const confirmDraftRun = confirmDraft.run;
  const confirmScope = useCallback(
    (
      understanding: string | undefined,
      draftPlan: boolean,
      onDone: (result: ClarifyConfirmResult) => void,
    ) => {
      const body: { understanding?: string; draft_plan?: boolean } = {};
      if (understanding) body.understanding = understanding;
      if (draftPlan) body.draft_plan = true;
      void (draftPlan ? confirmDraftRun : confirmRun)(clarifyPath(slug, "confirm"), {
        body,
        onSuccess: (result) => {
          if (result.state) apply(result.state);
          onDone(result);
        },
      });
    },
    [apply, confirmDraftRun, confirmRun, slug],
  );

  return { ask, save, confirm, confirmDraft, anyBusy, askQuestions, saveAnswers, confirmScope };
}
