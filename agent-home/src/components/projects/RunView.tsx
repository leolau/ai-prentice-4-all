"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  dateTimeLabel,
  durationLabel,
} from "@/components/projects/format";
import { unwrapRunEnvelope } from "@/components/projects/envelopes";
import { BusyRegion } from "@/components/ui/BusyRegion";
import { Pill, type Tone } from "@/components/ui/Pill";
import type { ProjectDelivery, ProjectRun, ProjectRunStatus } from "@/types";

const RUN_TONE: Record<ProjectRunStatus, Tone> = {
  running: "accent",
  waiting: "warning",
  blocked: "danger",
  done: "success",
  failed: "danger",
  cancelled: "muted",
};

function deliveryLabel(delivery: ProjectDelivery): string {
  const what = delivery.label ?? delivery.link_ref ?? "an artefact";
  const how =
    delivery.run_id != null
      ? "on a run"
      : delivery.task_id != null
        ? "from a card"
        : "by hand";
  return `${what} — delivered ${how}`;
}

/**
 * One run's page (§7): what it did — cards, deliveries, cost, outcome — and
 * the two things a human writes about it afterwards, the retro and (step 9b)
 * the score. Continue passes a checkpoint; Cancel stops promoting without
 * killing a worker; "Repeat this run" starts a new one on the same method.
 */
export function RunView({
  slug,
  run: initial,
  archived = false,
}: {
  slug: string;
  run: ProjectRun;
  archived?: boolean;
}) {
  const router = useRouter();
  const [run, setRun] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [budgetGate, setBudgetGate] = useState<string | null>(null);
  const [retroDraft, setRetroDraft] = useState(initial.retro ?? "");
  const [retroSaved, setRetroSaved] = useState(false);
  const [scoreDraft, setScoreDraft] = useState<number | null>(
    initial.score_user ?? null,
  );
  const [scoreNote, setScoreNote] = useState(initial.score_note ?? "");
  const [scoreSaved, setScoreSaved] = useState(false);

  const slugPath = `/api/projects/${encodeURIComponent(slug)}`;
  const runPath = `${slugPath}/runs/${run.run_no}`;

  const post = async (
    path: string,
    body?: Record<string, unknown>,
    /** Continue/cancel answer with the updated run row; merge it in. */
    mergeUpdatedRun = false,
  ): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      const data = (await res.json().catch(() => ({}))) as {
        detail?: string;
      };
      if (!res.ok) {
        setError(data.detail ?? "That did not go through.");
        return false;
      }
      if (mergeUpdatedRun) {
        // Continue answers with {run, promoted, budget_gate}; cancel with
        // the bare run row. Unwrap whichever came back.
        const { run: updated, budgetGate } = unwrapRunEnvelope(
          data as Record<string, unknown>,
        );
        if (updated) setRun((prev) => ({ ...prev, ...updated }));
        // The thing holding the run must be visible, not silent.
        setBudgetGate(budgetGate);
      }
      router.refresh(); // revalidate the page's server data after a write
      return true;
    } catch {
      setError("Could not reach the server.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const saveRetro = async () => {
    const text = retroDraft.trim();
    if (!text) return;
    setRetroSaved(false);
    if (await post(`${runPath}/retro`, { retro: text })) {
      setRun({ ...run, retro: text });
      setRetroSaved(true);
    }
  };

  /** §8.1: the human judgement — one tap, editable, never the agent's. */
  const saveScore = async () => {
    if (scoreDraft == null) return;
    setScoreSaved(false);
    const note = scoreNote.trim();
    const body: Record<string, unknown> = { score: scoreDraft };
    if (note) body.note = note;
    if (await post(`${runPath}/score`, body)) {
      setRun({ ...run, score_user: scoreDraft, score_note: note || null });
      setScoreSaved(true);
    }
  };

  const deliveries = Array.isArray(run.deliveries) ? run.deliveries : [];
  const cards = run.cards ?? [];
  const blockedTasks = run.blocked_tasks ?? [];
  const live =
    run.status === "running" ||
    run.status === "waiting" ||
    run.status === "blocked";
  // The row says running but the server saw no worker behind it — say so.
  const stalled = run.status === "running" && run.stalled === true;

  return (
    <div data-component="RunView" className="flex flex-col gap-4">
      <BusyRegion busy={busy} label={busy ? "Talking to the agent…" : undefined}>
        <div className="flex flex-col gap-4">
          {/* ── Headline ──────────────────────────────────────────── */}
          <header
            data-component="RunHeader"
            className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <div className="flex items-center gap-2">
              <h1 className="min-w-0 flex-1 truncate text-lg font-semibold">
                Run #{run.run_no}
              </h1>
              {stalled ? <Pill tone="danger">stalled</Pill> : null}
              <Pill tone={RUN_TONE[run.status]}>{run.status}</Pill>
            </div>
            <p className="mt-1 text-xs text-[var(--color-muted)]">
              {run.trigger} · on {run.profile} · started{" "}
              {dateTimeLabel(run.started_at)} ·{" "}
              {durationLabel(run.duration_seconds)}
              {run.playbook_rev != null ? ` · plan rev ${run.playbook_rev}` : ""}
            </p>
            <p className="mt-1 text-xs text-[var(--color-muted)]">
              cost:{" "}
              {run.cost != null
                ? `$${run.cost.toFixed(2)}`
                : run.cost_recorded === false
                  ? "not recorded"
                  : "—"}
            </p>
            {run.outcome ? (
              <p className="mt-2 text-sm">{run.outcome}</p>
            ) : null}
            {run.summary ? (
              <p className="mt-1 text-sm text-[var(--color-muted)]">
                {run.summary}
              </p>
            ) : null}
            {run.error ? (
              <p className="mt-1 text-sm text-red-400">{run.error}</p>
            ) : null}
            {run.score_user != null || run.score_self != null ? (
              <p className="mt-2 text-xs text-[var(--color-muted)]">
                score:
                {run.score_user != null ? ` ${run.score_user}/5 (you)` : ""}
                {run.score_self != null ? ` · ${run.score_self}/5 (self)` : ""}
                {run.score_note ? ` — ${run.score_note}` : ""}
              </p>
            ) : null}

            <div className="mt-3 flex flex-wrap gap-2">
              {run.status === "waiting" && !archived ? (
                <button
                  type="button"
                  onClick={() => void post(`${runPath}/continue`, undefined, true)}
                  disabled={busy}
                  className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                >
                  Continue
                </button>
              ) : null}
              {live ? (
                <button
                  type="button"
                  onClick={() => void post(`${runPath}/cancel`, undefined, true)}
                  disabled={busy}
                  className="rounded-xl border border-red-500/50 px-4 py-2 text-sm text-red-400 disabled:opacity-50"
                >
                  Cancel
                </button>
              ) : null}
              {(!live || stalled) && !archived ? (
                <button
                  type="button"
                  onClick={() =>
                    void post(
                      `${slugPath}/runs`,
                      run.playbook_rev != null
                        ? { playbook_rev: run.playbook_rev }
                        : {},
                    ).then((ok) => {
                      if (ok) router.push(`/projects/${encodeURIComponent(slug)}`);
                    })
                  }
                  disabled={busy}
                  className="rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm disabled:opacity-50"
                >
                  Repeat this run
                </button>
              ) : null}
            </div>

            {stalled ? (
              <p
                data-component="StallBanner"
                role="status"
                className="mt-2 rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-300"
              >
                Marked running, but no worker is active on this run — it is
                stalled. Cancel stops it; retry the blocked work below;
                Repeat starts a fresh run on the same method.
              </p>
            ) : null}

            {archived ? (
              <p className="mt-3 text-xs text-[var(--color-muted)]">
                This project is archived — restore it (⋯) to continue or
                score this run.
              </p>
            ) : null}

            {error ? (
              <p className="mt-2 text-sm text-red-400" role="alert">
                {error}
              </p>
            ) : null}
            {budgetGate ? (
              <p
                data-component="BudgetGate"
                role="status"
                className="mt-2 rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-300"
              >
                {budgetGate}
              </p>
            ) : null}
          </header>

          {/* ── Cards ─────────────────────────────────────────────── */}
          <section
            data-component="RunCards"
            className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Cards
            </h2>
            {cards.length === 0 ? (
              <p className="mt-2 text-sm text-[var(--color-muted)]">
                This run worked without cards.
              </p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1.5">
                {cards.map((card) => (
                  <li key={card.task_id}>
                    <Link
                      href={`/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(card.task_id)}`}
                      className="flex items-center gap-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm active:opacity-70"
                    >
                      <span className="min-w-0 flex-1 truncate">
                        {card.title ?? card.task_id}
                      </span>
                      <span className="text-xs text-[var(--color-muted)]">
                        {[card.step_key, card.status].filter(Boolean).join(" · ")}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* ── Blocked work ──────────────────────────────────────── */}
          {blockedTasks.length > 0 ? (
            <section
              data-component="RunBlocked"
              className="rounded-2xl border border-red-500/30 bg-[var(--color-surface)] p-4"
            >
              <h2 className="text-xs uppercase tracking-wide text-red-400">
                Blocked work
              </h2>
              <p className="mt-1 text-xs text-[var(--color-muted)]">
                These stopped the run. Open one to retry or stop it.
              </p>
              <ul className="mt-2 flex flex-col gap-1.5">
                {blockedTasks.map((task) => (
                  <li key={task.task_id}>
                    <Link
                      href={`/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(task.task_id)}`}
                      className="block rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm active:opacity-70"
                    >
                      <span className="block truncate">
                        {task.title ?? task.task_id}
                      </span>
                      {task.error ? (
                        <span className="block truncate text-xs text-red-400">
                          {task.error}
                        </span>
                      ) : null}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {/* ── Deliveries ────────────────────────────────────────── */}
          <section
            data-component="RunDeliveries"
            className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Delivered
            </h2>
            {deliveries.length === 0 ? (
              <p className="mt-2 text-sm text-[var(--color-muted)]">
                Nothing was delivered on this run.
              </p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1.5">
                {deliveries.map((delivery) => (
                  <li
                    key={delivery.id}
                    className="rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm"
                  >
                    {deliveryLabel(delivery)}
                    <span className="ml-2 text-xs text-[var(--color-muted)]">
                      {dateTimeLabel(delivery.delivered_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* ── Retro ─────────────────────────────────────────────── */}
          <section
            data-component="RunRetro"
            className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Retrospective
            </h2>
            <textarea
              className="mt-2 min-h-24 w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-sm"
              placeholder="What worked, what didn't, what to change next time…"
              readOnly={archived}
              value={retroDraft}
              onChange={(e) => {
                setRetroDraft(e.target.value);
                setRetroSaved(false);
              }}
            />
            {!archived ? (
              <div className="mt-2 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void saveRetro()}
                  disabled={busy || !retroDraft.trim()}
                  className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                >
                  Save retro
                </button>
                {retroSaved ? (
                  <span className="text-xs text-[var(--color-muted)]">saved</span>
                ) : null}
              </div>
            ) : null}
          </section>
          {/* ── Score ────────────────────────────────────────────── */}
          <section
            data-component="RunScore"
            className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Your score
            </h2>
            {!archived ? (
              <>
                <div
                  className="mt-2 flex gap-1.5"
                  role="group"
                  aria-label="Score this run from 1 to 5"
                >
                  {[1, 2, 3, 4, 5].map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => {
                        setScoreDraft(n);
                        setScoreSaved(false);
                      }}
                      disabled={busy}
                      aria-pressed={scoreDraft === n}
                      className={`h-9 w-9 rounded-xl border text-sm disabled:opacity-50 ${
                        scoreDraft === n
                          ? "border-[var(--color-accent)] bg-[var(--color-accent)] font-medium text-[var(--color-accent-fg)]"
                          : "border-[var(--color-border)] bg-[var(--color-surface-2)]"
                      }`}
                    >
                      {n}
                    </button>
                  ))}
                </div>
                <input
                  className="mt-2 w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-sm"
                  placeholder="One line on why — optional"
                  value={scoreNote}
                  onChange={(e) => {
                    setScoreNote(e.target.value);
                    setScoreSaved(false);
                  }}
                />
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void saveScore()}
                    disabled={busy || scoreDraft == null}
                    className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                  >
                    Save score
                  </button>
                  {scoreSaved ? (
                    <span className="text-xs text-[var(--color-muted)]">
                      saved
                    </span>
                  ) : null}
                </div>
              </>
            ) : null}
          </section>
        </div>
      </BusyRegion>
    </div>
  );
}
