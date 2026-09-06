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
