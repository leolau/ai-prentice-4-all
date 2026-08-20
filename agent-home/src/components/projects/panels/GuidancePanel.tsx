"use client";

import { useState } from "react";

import { agoLabel } from "@/components/projects/format";
import { prependDirective } from "@/components/projects/envelopes";
import { BusyRegion } from "@/components/ui/BusyRegion";
import type { ProjectDirective, ProjectDirectivesResponse } from "@/types";

/**
 * Standing instructions, newest-first (§5). The add field says what the rule
 * is — *applies from the next run* — because guidance never applies
 * mid-conversation. Retired instructions live behind a disclosure rather
 * than disappearing: the audit trail is the point.
 */
export function GuidancePanel({
  slug,
  initial,
  archived = false,
}: {
  slug: string;
  initial: ProjectDirectivesResponse | null;
  /** §13: a shelved project offers restore as the only write. */
  archived?: boolean;
}) {
  const [directives, setDirectives] = useState<ProjectDirective[]>(
    initial?.directives ?? [],
  );
  const [proposed, setProposed] = useState<ProjectDirective[]>(
    initial?.proposed ?? [],
  );
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [retired, setRetired] = useState<ProjectDirective[] | null>(null);
  const [loadingRetired, setLoadingRetired] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    const body = draft.trim();
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/directives`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ body }),
        },
      );
      if (!res.ok) throw new Error("add");
      // The full new row — body, author, date — with `applies_from` riding
      // flat beside it; prepend it so the instruction shows without a reload.
      const created = (await res.json()) as ProjectDirective & {
        applies_from?: string;
      };
      setDirectives((prev) => prependDirective(prev, created));
      setDraft("");
    } catch {
      setError("That didn't stick — try again.");
    } finally {
      setBusy(false);
    }
  };

  const retire = async (directiveId: string) => {
    setBusyId(directiveId);
    setError(null);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/directives/${encodeURIComponent(directiveId)}/retire`,
        { method: "POST" },
      );
      if (!res.ok) throw new Error("retire");
      setDirectives((prev) => prev.filter((row) => row.id !== directiveId));
    } catch {
      setError("That didn't stick — try again.");
    } finally {
      setBusyId(null);
    }
  };

  /** §8.2: a run proposed it in its retro; any member may cross it. */
  const activate = async (directiveId: string) => {
    setBusyId(directiveId);
    setError(null);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/directives/${encodeURIComponent(directiveId)}/activate`,
        { method: "POST" },
      );
      if (!res.ok) throw new Error("activate");
      const row = proposed.find((p) => p.id === directiveId);
      setProposed((prev) => prev.filter((p) => p.id !== directiveId));
      if (row) {
        setDirectives((prev) => [{ ...row, active: 1 }, ...prev]);
      }
    } catch {
      setError("That didn't stick — try again.");
    } finally {
      setBusyId(null);
    }
  };

  const loadRetired = async () => {
    setLoadingRetired(true);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(slug)}/directives?include_retired=true`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error("load");
      const body = (await res.json()) as ProjectDirectivesResponse;
      setRetired(body.directives.filter((row) => !row.active));
    } catch {
      setRetired([]);
    } finally {
      setLoadingRetired(false);
    }
  };

  return (
    <section
      id="panel-guidance"
      data-component="GuidancePanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Guidance
      </h2>

      {initial == null ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Guidance is unavailable right now.
        </p>
      ) : (
        <>
          <ul className="mt-2 flex flex-col gap-2">
            {directives.map((directive) => (
              <li
                key={directive.id}
                data-component="DirectiveRow"
                className="rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm"
              >
                <p>{directive.body}</p>
                <p className="mt-1 flex items-center justify-between text-xs text-[var(--color-muted)]">
                  <span>
                    {directive.author_user_id} ·{" "}
                    {agoLabel(directive.created_at)}
                    {directive.kind === "feedback" ? " · feedback" : ""}
                  </span>
                  {archived ? null : (
                    <BusyRegion busy={busyId === directive.id} label="Retiring…">
                      <button
                        type="button"
                        onClick={() => void retire(directive.id)}
                        className="text-[var(--color-muted)] underline"
                      >
                        Retire
                      </button>
                    </BusyRegion>
                  )}
                </p>
              </li>
            ))}
            {directives.length === 0 ? (
              <li className="text-sm text-[var(--color-muted)]">
                No standing instructions yet.
              </li>
            ) : null}
          </ul>

          {proposed.length > 0 ? (
            <div className="mt-3" data-component="ProposedDirectives">
              <h3 className="text-xs font-medium text-[var(--color-muted)]">
                Proposed by runs — inactive until you activate
              </h3>
              <ul className="mt-1.5 flex flex-col gap-2">
                {proposed.map((directive) => (
                  <li
                    key={directive.id}
                    className="rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-sm"
                  >
                    <p>{directive.body}</p>
                    <p className="mt-1 flex items-center justify-between text-xs text-[var(--color-muted)]">
                      <span>
                        {directive.author_user_id} ·{" "}
                        {agoLabel(directive.created_at)}
                      </span>
                      {archived ? null : (
                        <BusyRegion
                          busy={busyId === directive.id}
                          label="Activating…"
                        >
                          <button
                            type="button"
                            onClick={() => void activate(directive.id)}
                            className="text-[var(--color-accent)] underline"
                          >
                            Activate
                          </button>
                        </BusyRegion>
                      )}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {error ? (
            <p className="mt-2 text-sm text-red-300">{error}</p>
          ) : null}

          {archived ? (
            <p className="mt-3 text-xs text-[var(--color-muted)]">
              This project is archived — restore it (⋯) to add guidance.
            </p>
          ) : (
            <BusyRegion busy={busy} label="Adding instruction…" className="mt-3">
              <div className="flex flex-col gap-1.5">
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Add an instruction…"
                  rows={2}
                  className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
                />
                <div className="flex items-center justify-between">
                  <span className="text-xs text-[var(--color-muted)]">
                    {initial.applies_from}
                  </span>
                  <button
                    type="button"
                    onClick={() => void add()}
                    disabled={!draft.trim()}
                    className="rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] disabled:opacity-40"
                  >
                    Add
                  </button>
                </div>
              </div>
            </BusyRegion>
          )}

          {retired == null ? (
            <button
              type="button"
              onClick={() => void loadRetired()}
              disabled={loadingRetired}
              className="mt-3 text-xs text-[var(--color-muted)] underline"
            >
              {loadingRetired ? "Loading…" : "Show retired instructions"}
            </button>
          ) : retired.length > 0 ? (
            <ul className="mt-2 flex flex-col gap-1.5" data-component="RetiredDirectives">
              {retired.map((directive) => (
                <li
                  key={directive.id}
                  className="rounded-lg bg-[var(--color-surface-2)] px-3 py-1.5 text-xs text-[var(--color-muted)] line-through"
                >
                  {directive.body}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-[var(--color-muted)]">
              Nothing retired yet.
            </p>
          )}
        </>
      )}
    </section>
  );
}
