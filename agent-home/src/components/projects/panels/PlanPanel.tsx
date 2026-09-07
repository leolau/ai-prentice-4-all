"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { friendlyError } from "@/components/projects/errors";

import { dateTimeLabel } from "@/components/projects/format";
import { BusyRegion } from "@/components/ui/BusyRegion";
import type {
  PlaybookRev,
  PlaybookStep,
  ProjectPlaybookDraftState,
  ProjectPlaybookResponse,
} from "@/types";

/** How often the panel asks the server whether the agent's draft is ready. */
export const DRAFT_POLL_MS = 2_000;

interface StepDraft {
  title: string;
  assignee: string;
  after: string;
  checkpoint: boolean;
}

const EMPTY_STEP: StepDraft = { title: "", assignee: "", after: "", checkpoint: false };

/** `Send the Monday digest` → `send-the-monday-digest`; unique within the list. */
export function stepKey(title: string, index: number, taken: Set<string>): string {
  const base =
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40) || `step-${index + 1}`;
  let key = base;
  let n = 2;
  while (taken.has(key)) key = `${base}-${n++}`;
  taken.add(key);
  return key;
}

function stepsToPayload(drafts: StepDraft[]): PlaybookStep[] {
  const taken = new Set<string>();
  const keyed = drafts
    .filter((d) => d.title.trim())
    .map((d, i) => ({ draft: d, key: stepKey(d.title.trim(), i, taken) }));
  const byTitle = new Map(keyed.map((k) => [k.draft.title.trim(), k.key]));
  return keyed.map(({ draft, key }) => ({
    key,
    title: draft.title.trim(),
    assignee: draft.assignee || null,
    depends_on: draft.after && byTitle.has(draft.after) ? [byTitle.get(draft.after)!] : [],
    checkpoint: draft.checkpoint,
  }));
}

function draftsFromRev(rev: PlaybookRev | null): StepDraft[] {
  const steps = rev?.steps ?? [];
  if (steps.length === 0) return [{ ...EMPTY_STEP }];
  const titleByKey = new Map(steps.map((s) => [s.key, s.title]));
  return steps.map((s) => ({
    title: s.title,
    assignee: s.assignee ?? "",
    after: titleByKey.get((s.depends_on ?? s.needs ?? [])[0] ?? "") ?? "",
    checkpoint: Boolean(s.checkpoint),
  }));
}

/**
 * The active playbook, labelled **Plan** — "playbook" is our word, not the
 * user's (§13). Prose plus steps as an indented list, its revision and who
 * activated it. Proposed revisions (§8.2 — a retro's write-back, a draft
 * saved here, or one the agent drafted on request) render beneath with the
 * lead's Activate; saving is open to any member, activation stays a human
 * lead/admin act (§7.2).
 *
 * "Draft with the agent" starts a server-side job (`POST …/playbook/draft`)
 * and polls until the proposal lands — nothing enters the user's chat, and
 * a reload while it runs resumes the wait.
 */
export function PlanPanel({
  slug,
  playbook,
  profiles,
  canActivate,
  archived,
}: {
  slug: string;
  playbook: ProjectPlaybookResponse | null;
  /** The project's profiles — the only legal step assignees. */
  profiles: string[];
  canActivate: boolean;
  archived: boolean;
}) {
  const router = useRouter();
  const active = playbook?.active ?? null;
  const proposed = (playbook?.revisions ?? []).filter((rev) => !rev.active);

  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState("");
  const [steps, setSteps] = useState<StepDraft[]>([{ ...EMPTY_STEP }]);
  const [busy, setBusy] = useState(false);
  const [busyRev, setBusyRev] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [draftNotice, setDraftNotice] = useState<string | null>(null);

  const slugPath = `/api/projects/${encodeURIComponent(slug)}`;

  const settleDraft = useCallback(
    (state: ProjectPlaybookDraftState) => {
      if (state.status === "running") {
        setDrafting(true);
        return;
      }
      setDrafting(false);
      if (state.status === "done") {
        setDraftNotice(
          `The agent proposed revision ${state.rev ?? ""} — review it below and activate it when it looks right.`,
        );
        router.refresh();
      } else if (state.status === "failed") {
        setError(
          friendlyError(
            { status: 502, detail: state.detail },
            "The agent could not draft a plan — try again, or write it yourself.",
          ),
        );
      }
    },
    [router],
  );

  // On mount: resume waiting on a draft started before a reload. While
  // drafting: poll until the job settles. Both use the same GET.
  const unavailable = playbook == null;
  useEffect(() => {
    if (archived || unavailable) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const res = await fetch(`${slugPath}/playbook/draft`);
        const state = (await res.json().catch(() => ({}))) as ProjectPlaybookDraftState;
        if (cancelled) return;
        if (!res.ok || !state.status) {
          setDrafting(false);
          return;
        }
        if (state.status === "running") {
          setDrafting(true);
          timer = setTimeout(() => void poll(), DRAFT_POLL_MS);
          return;
        }
        // A finished job seen on first mount is old news; only report the
        // outcome of a draft this panel was waiting on.
        if (drafting) settleDraft(state);
      } catch {
        if (cancelled) return;
        setDrafting(false);
        if (drafting) setError("Could not reach the server.");
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [archived, unavailable, slugPath, drafting, settleDraft]);

  const draftWithAgent = async () => {
    setError(null);
    setDraftNotice(null);
    try {
      const res = await fetch(`${slugPath}/playbook/draft`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      });
      const data = (await res.json().catch(() => ({}))) as ProjectPlaybookDraftState & {
        detail?: string;
      };
      if (!res.ok) {
        setError(
          friendlyError(
            { status: res.status, detail: data.detail },
            "The agent could not start drafting.",
          ),
        );
        return;
      }
      settleDraft(data);
    } catch {
      setError("Could not reach the server.");
    }
  };

  const openEditor = (from: PlaybookRev | null) => {
    setBody(from?.body ?? "");
    setSteps(draftsFromRev(from));
    setError(null);
    setEditing(true);
  };

  const save = async () => {
    const payload = stepsToPayload(steps);
    if (payload.length === 0) {
      setError("A plan needs at least one step with a title.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${slugPath}/playbook`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ body: body.trim(), steps: payload }),
      });
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) {
        setError(friendlyError({ status: res.status, detail: data.detail }, "The plan was not saved."));
        return;
      }
      setEditing(false);
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const activate = async (rev: number) => {
    setBusyRev(rev);
    setError(null);
    try {
      const res = await fetch(`${slugPath}/playbook/${rev}/activate`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) {
        setError(friendlyError({ status: res.status, detail: data.detail }, "Activation was refused."));
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusyRev(null);
    }
  };

  const inputClass =
    "w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-sm outline-none focus:border-[var(--color-accent)]";

  return (
    <section
      id="panel-plan"
      data-component="PlanPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <div className="flex items-center gap-2">
        <h2 className="flex-1 text-xs uppercase tracking-wide text-[var(--color-muted)]">
          Plan
        </h2>
        {!archived && !editing && playbook != null ? (
          <button
            type="button"
            data-component="DraftPlanWithAgent"
            onClick={() => void draftWithAgent()}
            disabled={drafting}
            className="rounded-lg border border-[var(--color-border)] px-2.5 py-1 text-xs disabled:opacity-50"
          >
            {drafting ? "Agent is drafting…" : "Draft with the agent"}
          </button>
        ) : null}
        {!archived && !editing ? (
          <button
            type="button"
            onClick={() => openEditor(active)}
            className="rounded-lg border border-[var(--color-border)] px-2.5 py-1 text-xs"
          >
            {active ? "Revise" : "Write plan"}
          </button>
        ) : null}
      </div>

      {playbook == null ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          The plan is unavailable right now.
        </p>
      ) : (
        <>
          {active == null && !editing && !drafting && proposed.length === 0 ? (
            <p className="mt-2 text-sm text-[var(--color-muted)]">
              No active plan yet — a run needs one. Ask the agent to draft one
              from the brief, or write it here; either way you activate it
              when it looks right.
            </p>
          ) : null}

          {drafting ? (
            <p
              data-component="DraftPlanProgress"
              role="status"
              className="mt-2 text-sm text-[var(--color-muted)]"
            >
              The agent is reading the brief and drafting steps — usually under
              a minute. It will appear below as a proposed revision; you can
              leave this page, the draft continues on the server.
            </p>
          ) : null}

          {draftNotice ? (
            <p role="status" className="mt-2 text-sm text-[var(--color-accent)]">
              {draftNotice}
            </p>
          ) : null}

          {active != null ? (
            <>
              {active.body ? (
                <p className="mt-2 whitespace-pre-wrap text-sm">
                  {active.body}
                </p>
              ) : null}
              {active.steps && active.steps.length > 0 ? (
                <ol className="mt-2 flex flex-col gap-1">
                  {active.steps.map((step, index) => (
                    <li
                      key={step.key}
                      className="flex items-baseline gap-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-1.5 text-sm"
                      style={{
                        marginLeft: `${((step.depends_on ?? step.needs)?.length ?? 0) > 0 ? 1 : 0}rem`,
                      }}
                    >
                      <span className="text-xs text-[var(--color-muted)]">
                        {index + 1}.
                      </span>
                      <span className="min-w-0 flex-1">
                        {step.title}
                        {step.checkpoint ? (
                          <span className="ml-2 rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-300">
                            checkpoint
                          </span>
                        ) : null}
                        {step.assignee ? (
                          <span className="ml-2 text-xs text-[var(--color-muted)]">
                            → {step.assignee}
                          </span>
                        ) : null}
                      </span>
                    </li>
                  ))}
                </ol>
              ) : null}
              <p className="mt-2 text-xs text-[var(--color-muted)]">
                revision {active.rev}
                {active.note ? ` · ${active.note}` : ""}
                {active.activated_at != null
                  ? ` · activated ${dateTimeLabel(active.activated_at)}`
                  : ""}
                {active.created_by ? ` · by ${active.created_by}` : ""}
              </p>
            </>
          ) : null}

          {editing ? (
            <BusyRegion busy={busy} label="Saving the plan…">
              <form
                data-component="PlanEditor"
                className="mt-3 flex flex-col gap-3 rounded-xl border border-dashed border-[var(--color-border)] p-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  void save();
                }}
              >
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-xs text-[var(--color-muted)]">
                    How the agent should approach it (optional)
                  </span>
                  <textarea
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    rows={3}
                    placeholder="Gather this week's arrivals, draft the digest, send it after a checkpoint…"
                    className={inputClass}
                  />
                </label>

                <div className="flex flex-col gap-2">
                  <span className="text-xs text-[var(--color-muted)]">
                    Steps — in order; each becomes a card on the board
                  </span>
                  {steps.map((step, index) => (
                    <div
                      key={index}
                      data-component="PlanStepRow"
                      className="flex flex-col gap-1.5 rounded-lg bg-[var(--color-surface-2)] p-2"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-[var(--color-muted)]">
                          {index + 1}.
                        </span>
                        <input
                          value={step.title}
                          onChange={(e) =>
                            setSteps((prev) =>
                              prev.map((s, i) =>
                                i === index ? { ...s, title: e.target.value } : s,
                              ),
                            )
                          }
                          placeholder="What this step delivers"
                          aria-label={`Step ${index + 1} title`}
                          className={inputClass}
                        />
                        {steps.length > 1 ? (
                          <button
                            type="button"
                            aria-label={`Remove step ${index + 1}`}
                            onClick={() =>
                              setSteps((prev) => prev.filter((_s, i) => i !== index))
                            }
                            className="rounded-lg border border-[var(--color-border)] px-2 text-sm text-[var(--color-muted)]"
                          >
                            ×
                          </button>
                        ) : null}
                      </div>
                      <div className="flex flex-wrap items-center gap-2 pl-5 text-xs">
                        <select
                          value={step.assignee}
                          onChange={(e) =>
                            setSteps((prev) =>
                              prev.map((s, i) =>
                                i === index ? { ...s, assignee: e.target.value } : s,
                              ),
                            )
                          }
                          aria-label={`Step ${index + 1} assignee`}
                          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1"
                        >
                          <option value="">any profile</option>
                          {profiles.map((p) => (
                            <option key={p} value={p}>
                              {p}
                            </option>
                          ))}
                        </select>
                        {index > 0 ? (
                          <select
                            value={step.after}
                            onChange={(e) =>
                              setSteps((prev) =>
                                prev.map((s, i) =>
                                  i === index ? { ...s, after: e.target.value } : s,
                                ),
                              )
                            }
                            aria-label={`Step ${index + 1} waits for`}
                            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1"
                          >
                            <option value="">can start right away</option>
                            {steps
                              .slice(0, index)
                              .filter((s) => s.title.trim())
                              .map((s) => (
                                <option key={s.title} value={s.title.trim()}>
                                  after “{s.title.trim()}”
                                </option>
                              ))}
                          </select>
                        ) : null}
                        <label className="flex items-center gap-1">
                          <input
                            type="checkbox"
                            checked={step.checkpoint}
                            onChange={(e) =>
                              setSteps((prev) =>
                                prev.map((s, i) =>
                                  i === index
                                    ? { ...s, checkpoint: e.target.checked }
                                    : s,
                                ),
                              )
                            }
                          />
                          pause for my approval after
                        </label>
                      </div>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() => setSteps((prev) => [...prev, { ...EMPTY_STEP }])}
                    className="self-start rounded-lg border border-[var(--color-border)] px-2.5 py-1 text-xs"
                  >
                    Add a step
                  </button>
                </div>

                <p className="text-xs text-[var(--color-muted)]">
                  Saving proposes a new revision; nothing changes until a lead
                  activates it.
                </p>

                <div className="flex items-center justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setEditing(false)}
                    className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-sm"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={busy}
                    className="rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                  >
                    Save as revision
                  </button>
                </div>
              </form>
            </BusyRegion>
          ) : null}

          {proposed.length > 0 ? (
            <div className="mt-3" data-component="ProposedRevisions">
              <h3 className="text-xs font-medium text-[var(--color-muted)]">
                Proposed revisions — awaiting activation
              </h3>
              <ul className="mt-1.5 flex flex-col gap-2">
                {proposed.map((rev) => (
                  <li
                    key={rev.rev}
                    className="rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-sm"
                  >
                    {rev.body ? (
                      <p className="whitespace-pre-wrap">{rev.body}</p>
                    ) : null}
                    {(rev.steps ?? []).length > 0 ? (
                      <ol className="mt-1 flex flex-col gap-0.5 text-xs">
                        {(rev.steps ?? []).map((step, index) => (
                          <li key={step.key}>
                            {index + 1}. {step.title}
                            {step.assignee ? ` → ${step.assignee}` : ""}
                            {step.checkpoint ? " · checkpoint" : ""}
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="text-[var(--color-muted)]">no steps</p>
                    )}
                    <div className="mt-1.5 flex items-center gap-2">
                      <p className="min-w-0 flex-1 text-xs text-[var(--color-muted)]">
                        revision {rev.rev}
                        {rev.note ? ` · ${rev.note}` : ""}
                        {rev.created_by ? ` · by ${rev.created_by}` : ""}
                      </p>
                      {!archived && canActivate ? (
                        <button
                          type="button"
                          onClick={() => void activate(rev.rev)}
                          disabled={busyRev != null}
                          className="rounded-lg bg-[var(--color-accent)] px-2.5 py-1 text-xs font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                        >
                          {busyRev === rev.rev ? "Activating…" : "Activate"}
                        </button>
                      ) : null}
                      {!archived && !canActivate ? (
                        <span className="text-xs text-[var(--color-muted)]">
                          a lead activates
                        </span>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {error ? (
            <p role="alert" className="mt-2 text-sm text-red-400">
              {error}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
