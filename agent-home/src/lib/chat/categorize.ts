import type { SessionSummary } from "@/types";

export type ChatCategory = "kanban" | "daily" | "scheduled" | "others";

export const CHAT_CATEGORY_LABELS: Record<ChatCategory, string> = {
  kanban: "Kanban",
  daily: "Daily",
  scheduled: "Scheduled",
  others: "Others",
};

/** Display order for category sections. */
export const CHAT_CATEGORY_ORDER: ChatCategory[] = [
  "kanban",
  "daily",
  "scheduled",
  "others",
];

/**
 * Best-effort detector for a kanban card worker's session: its `cwd` sits
 * under a kanban scratch-workspace root. Covers the default board
 * (`.../kanban/workspaces/<task_id>`) and named boards
 * (`.../kanban/boards/<slug>/workspaces/<task_id>`) — see
 * `kanban_db.workspaces_root()`. Does not attempt to catch project-linked
 * *worktree* kanban tasks (`.../.worktrees/<task_id>`), since that path
 * shape is shared with ordinary git worktrees unrelated to kanban at all —
 * a false positive there is worse than a false negative here.
 */
function isKanbanWorkspace(cwd: string | null | undefined): boolean {
  if (!cwd) return false;
  return cwd.includes("/kanban/workspaces/") || cwd.includes("/kanban/boards/");
}

function isSameLocalDay(epochSeconds: number | null, now: Date): boolean {
  if (epochSeconds === null || !Number.isFinite(epochSeconds)) return false;
  const d = new Date(epochSeconds * 1000);
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

/**
 * Classify a session into one of four display categories, in priority
 * order: a kanban-worker session is "kanban" even if it also happened
 * today; a cron-triggered session is "scheduled" even if today; anything
 * else from today is "daily"; everything older falls to "others".
 */
export function categorizeSession(
  session: Pick<SessionSummary, "source" | "cwd" | "started_at">,
  now: Date = new Date(),
): ChatCategory {
  if (isKanbanWorkspace(session.cwd)) return "kanban";
  if (session.source === "cron") return "scheduled";
  if (isSameLocalDay(session.started_at, now)) return "daily";
  return "others";
}

/** Group a session list into the four categories, preserving each
 * category's relative order from the input array. */
export function groupSessionsByCategory(
  sessions: SessionSummary[],
  now: Date = new Date(),
): Record<ChatCategory, SessionSummary[]> {
  const groups: Record<ChatCategory, SessionSummary[]> = {
    kanban: [],
    daily: [],
    scheduled: [],
    others: [],
  };
  for (const s of sessions) {
    groups[categorizeSession(s, now)].push(s);
  }
  return groups;
}
