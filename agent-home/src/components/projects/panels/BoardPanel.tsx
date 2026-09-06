"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { cardMoves } from "@/components/projects/cardMoves";
import { BusyRegion } from "@/components/ui/BusyRegion";
import type { ProjectBoardView } from "@/types";

/**
 * Columns a human may start a card in. Every card is born in `triage`
 * (§10: a project asking for work is not a human approving it); "New card"
 * in `ready` creates it and approves it in one gesture.
 */
export const NEW_CARD_COLUMNS = ["triage", "ready"] as const;

/**
 * The project's cards, one column per stage. On a phone each column snaps to
 * the screen (‹ › by swipe); from `md:` up the columns flow side by side.
 * Cards move between columns from the row itself; each startable column
 * offers "New card". The board read is fan-out safe: when it is unavailable
 * the rest of the page still renders.
 */
export function BoardPanel({
  slug,
  board,
  archived = false,
}: {
  slug: string;
  board: ProjectBoardView | null;
  archived?: boolean;
}) {
  const router = useRouter();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newIn, setNewIn] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");

  const base = `/api/projects/${encodeURIComponent(slug)}/cards`;

  const request = async (
    path: string,
    method: "POST" | "PATCH",
    body: Record<string, unknown>,
  ): Promise<Record<string, unknown> | null> => {
    const res = await fetch(path, {
      method,
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok) {
      setError(
        typeof data.detail === "string" ? data.detail : "That did not go through.",
      );
      return null;
    }
    return data;
  };

  const move = async (taskId: string, to: string) => {
    setBusyId(taskId);
    setError(null);
    try {
      const ok = await request(
        `${base}/${encodeURIComponent(taskId)}`,
        "PATCH",
        { status: to },
      );
      if (ok) router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusyId(null);
    }
  };

  const create = async (column: string) => {
    const title = newTitle.trim();
    if (!title) {
      setError("A card needs a title.");
      return;
    }
    setBusyId(`new:${column}`);
    setError(null);
    try {
      const made = await request(base, "POST", { title });
      if (!made) return;
      const taskId = typeof made.task_id === "string" ? made.task_id : null;
      if (column !== "triage" && taskId) {
        const moved = await request(
          `${base}/${encodeURIComponent(taskId)}`,
          "PATCH",
          { status: column },
        );
        if (!moved) {
          setError((prev) => `${prev ?? ""} The card was created in triage.`.trim());
        }
      }
      setNewTitle("");
      setNewIn(null);
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusyId(null);
    }
  };

  const canStartIn = (column: string) =>
    !archived && (NEW_CARD_COLUMNS as readonly string[]).includes(column);

  return (
    <section
      id="panel-board"
      data-component="BoardPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Board
      </h2>

      {board == null ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          The board is unavailable right now — the profile it lives on could
          not be reached.
        </p>
      ) : (
        <>
          {board.columns.every((column) => column.tasks.length === 0) ? (
            <p className="mt-2 text-sm text-[var(--color-muted)]">
              No cards yet — start one in a column below, add work from the
              header&rsquo;s Add sheet, or promote a to-do into this project.
            </p>
          ) : null}
          <div className="mt-2 flex snap-x snap-mandatory gap-3 overflow-x-auto pb-1 md:grid md:grid-cols-3 md:overflow-visible">
            {board.columns.map((column) => (
              <div
                key={column.name}
                data-component="BoardColumn"
                className="min-w-[85%] snap-center rounded-xl bg-[var(--color-surface-2)] p-2 md:min-w-0"
              >
                <div className="flex items-center px-1 py-1">
                  <p className="flex-1 text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
                    {column.name}
                    {column.tasks.length > 0 ? ` · ${column.tasks.length}` : ""}
                  </p>
                  {canStartIn(column.name) ? (
                    <button
                      type="button"
                      onClick={() => {
                        setError(null);
                        setNewIn(newIn === column.name ? null : column.name);
                      }}
                      disabled={busyId !== null}
                      className="rounded-md px-1.5 py-0.5 text-xs text-[var(--color-accent)] disabled:opacity-40"
                    >
                      + New card
                    </button>
                  ) : null}
                </div>
                {newIn === column.name ? (
                  <form
                    data-component="NewCardForm"
                    className="mb-1.5 flex flex-col gap-1.5"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void create(column.name);
                    }}
                  >
                    <BusyRegion
                      busy={busyId === `new:${column.name}`}
                      label="Creating…"
                    >
                      <input
                        autoFocus
                        value={newTitle}
                        onChange={(e) => setNewTitle(e.target.value)}
                        placeholder="What needs doing?"
                        aria-label={`New card in ${column.name}`}
                        className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-sm outline-none focus:border-[var(--color-accent)]"
                      />
                    </BusyRegion>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => {
                          setNewIn(null);
                          setNewTitle("");
                        }}
                        className="rounded-lg px-2 py-1 text-xs text-[var(--color-muted)]"
                      >
                        Cancel
                      </button>
                      <span className="flex-1" />
                      <button
                        type="submit"
                        disabled={busyId !== null}
                        className="rounded-lg bg-[var(--color-accent)] px-2.5 py-1 text-xs font-medium text-[var(--color-accent-fg)] disabled:opacity-40"
                      >
                        Create
                      </button>
                    </div>
                  </form>
                ) : null}
                <ul className="flex flex-col gap-1.5">
                  {column.tasks.map((task) => {
                    const moves = archived ? [] : cardMoves(task.status);
                    return (
                      <li
                        key={task.id}
                        data-component="BoardCard"
                        className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
                      >
                        <Link
                          href={`/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(task.id)}`}
                          className="block px-2.5 py-2 text-sm active:opacity-70"
                        >
                          <span className="block truncate">{task.title}</span>
                          <span className="block truncate text-xs text-[var(--color-muted)]">
                            {task.assignee ?? "unassigned"}
                            {task.current_step_key
                              ? ` · ${task.current_step_key}`
                              : ""}
                          </span>
                        </Link>
                        {moves.length > 0 ? (
                          <div className="flex items-center gap-1 px-2.5 pb-1.5">
                            <select
                              aria-label={`Move ${task.title}`}
                              value=""
                              disabled={busyId !== null}
                              onChange={(e) => {
                                if (e.target.value) void move(task.id, e.target.value);
                              }}
                              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface-2)] px-1.5 py-1 text-xs disabled:opacity-40"
                            >
                              <option value="">
                                {busyId === task.id ? "Moving…" : "Move to…"}
                              </option>
                              {moves.map((m) => (
                                <option key={m.to} value={m.to}>
                                  {m.label}
                                </option>
                              ))}
                            </select>
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </>
      )}

      {error ? (
        <p role="alert" className="mt-2 text-sm text-red-400">
          {error}
        </p>
      ) : null}
    </section>
  );
}
