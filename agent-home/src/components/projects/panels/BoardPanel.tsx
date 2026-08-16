import Link from "next/link";

import type { ProjectBoardView } from "@/types";

/**
 * The project's cards, one column per stage. On a phone each column snaps to
 * the screen (‹ › by swipe); from `md:` up the columns flow side by side.
 * The board read is fan-out safe: when it is unavailable the rest of the
 * page still renders.
 */
export function BoardPanel({
  slug,
  board,
}: {
  slug: string;
  board: ProjectBoardView | null;
}) {
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
      ) : board.columns.every((column) => column.tasks.length === 0) ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          No cards yet — add work from the header&rsquo;s Add sheet, or
          promote a to-do into this project.
        </p>
      ) : (
        <div className="mt-2 flex snap-x snap-mandatory gap-3 overflow-x-auto pb-1 md:grid md:grid-cols-3 md:overflow-visible">
          {board.columns.map((column) => (
            <div
              key={column.name}
              data-component="BoardColumn"
              className="min-w-[85%] snap-center rounded-xl bg-[var(--color-surface-2)] p-2 md:min-w-0"
            >
              <p className="px-1 py-1 text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
                {column.name}
                {column.tasks.length > 0 ? ` · ${column.tasks.length}` : ""}
              </p>
              <ul className="flex flex-col gap-1.5">
                {column.tasks.map((task) => (
                  <li key={task.id}>
                    <Link
                      href={`/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(task.id)}`}
                      className="block rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-2 text-sm active:opacity-70"
                    >
                      <span className="block truncate">{task.title}</span>
                      <span className="block truncate text-xs text-[var(--color-muted)]">
                        {task.assignee ?? "unassigned"}
                        {task.current_step_key ? ` · ${task.current_step_key}` : ""}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
