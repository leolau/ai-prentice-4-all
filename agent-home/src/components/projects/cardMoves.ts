/**
 * The column moves a human may make by hand, mirroring the backend's
 * `PATCH /{slug}/cards/{id}` routing: each lands on the structured kanban
 * verb that owns the transition. `running`, `review`, `scheduled` and
 * `todo` are the dispatcher's / worker's and never appear as a target
 * (approving a triage card runs the ready pass, which parks it in `todo`
 * only while a parent is still open).
 */
export interface CardMove {
  to: string;
  label: string;
}

const MOVES: Record<string, CardMove[]> = {
  triage: [
    { to: "ready", label: "Approve — make ready" },
    { to: "archived", label: "Archive" },
  ],
  todo: [
    { to: "ready", label: "Approve — make ready" },
    { to: "archived", label: "Archive" },
  ],
  scheduled: [
    { to: "ready", label: "Make ready now" },
    { to: "archived", label: "Archive" },
  ],
  ready: [
    { to: "done", label: "Mark done" },
    { to: "blocked", label: "Block" },
    { to: "archived", label: "Archive" },
  ],
  running: [
    { to: "done", label: "Mark done" },
    { to: "blocked", label: "Block" },
  ],
  blocked: [
    { to: "ready", label: "Unblock — make ready" },
    { to: "archived", label: "Archive" },
  ],
  review: [{ to: "archived", label: "Archive" }],
  done: [{ to: "archived", label: "Archive" }],
  archived: [],
};

export function cardMoves(status: string): CardMove[] {
  return MOVES[status] ?? [{ to: "archived", label: "Archive" }];
}

import type { ProjectBoardTask, ProjectBoardView } from "@/types";

/**
 * Move one task to another column in a local board copy — the optimistic
 * half of a card move: the UI applies this immediately, then PATCHes, then
 * `router.refresh()` replaces the copy with the server's board. The card
 * lands at the end of the destination column and its `status` follows the
 * column name. Returns the board unchanged when the task or the column
 * isn't there (a refresh arriving mid-move is handled by `useServerState`,
 * not here).
 */
export function moveTaskInBoard(
  board: ProjectBoardView,
  taskId: string,
  to: string,
): ProjectBoardView {
  if (!board.columns.some((col) => col.name === to)) return board;
  let moved: ProjectBoardTask | null = null;
  const without = board.columns.map((col) => ({
    ...col,
    tasks: col.tasks.filter((task) => {
      if (task.id === taskId) {
        moved = task;
        return false;
      }
      return true;
    }),
  }));
  if (!moved) return board;
  const stamped: ProjectBoardTask = { ...(moved as ProjectBoardTask), status: to };
  return {
    ...board,
    columns: without.map((col) =>
      col.name === to ? { ...col, tasks: [...col.tasks, stamped] } : col,
    ),
  };
}

/**
 * The minimal card row for an optimistic "New card" insert — the POST only
 * answers with the new id, so everything else is a placeholder the next
 * server refresh replaces.
 */
export function newBoardCard(
  taskId: string,
  title: string,
  status: string,
): ProjectBoardTask {
  return {
    id: taskId,
    title,
    body: null,
    status,
    assignee: null,
    priority: 0,
    created_at: Math.floor(Date.now() / 1000),
    started_at: null,
    completed_at: null,
    tenant: null,
    project_id: null,
    result: null,
    current_step_key: null,
  };
}

/**
 * Append a freshly created card to a column in a local board copy (the
 * optimistic half of "New card"). The POST answers with only the new id, so
 * the caller builds a minimal task row; the refresh that follows replaces
 * it with the server's authoritative row.
 */
export function addTaskToBoard(
  board: ProjectBoardView,
  columnName: string,
  task: ProjectBoardTask,
): ProjectBoardView {
  return {
    ...board,
    columns: board.columns.map((col) =>
      col.name === columnName ? { ...col, tasks: [...col.tasks, task] } : col,
    ),
  };
}

/** Only the fields that changed — the API treats every key as an assignment. */
export function cardPatch(
  before: { title: string; body: string; assignee: string },
  after: { title: string; body: string; assignee: string },
): { title?: string; body?: string; assignee?: string | null } {
  const patch: { title?: string; body?: string; assignee?: string | null } = {};
  if (after.title.trim() !== before.title.trim()) patch.title = after.title.trim();
  if (after.body !== before.body) patch.body = after.body;
  if (after.assignee !== before.assignee) patch.assignee = after.assignee || null;
  return patch;
}
