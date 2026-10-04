import type {
  ProjectDetail,
  ProjectDirective,
  ProjectDirectivesResponse,
} from "@/types";

export interface RequirementLine {
  key: string;
  text: string;
  /** The newest requirement change, highlighted. */
  isNew: boolean;
  author: string | null;
  at: number | null;
}

export interface RequirementsSummary {
  lines: RequirementLine[];
  /** 1 + every requirement change (directive) ever recorded. */
  version: number;
}

const MAX_TEXT = 140;

function clip(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > MAX_TEXT ? `${flat.slice(0, MAX_TEXT - 1)}…` : flat;
}

/**
 * The brief's key points (goal, deliverables, done-when, audience) then the
 * active project-wide directives oldest → newest, the newest one flagged.
 */
export function requirementsSummary(
  project: ProjectDetail,
  directives: ProjectDirectivesResponse | null,
): RequirementsSummary {
  const lines: RequirementLine[] = [];
  const push = (key: string, text: string | null | undefined) => {
    if (text && text.trim()) {
      lines.push({ key, text: clip(text), isNew: false, author: null, at: null });
    }
  };
  push("goal", project.goal);
  const outputs = project.outputs.filter((o) => o.status !== "dropped");
  if (outputs.length > 0) {
    push("outputs", `Deliver: ${outputs.map((o) => o.title).join(" · ")}`);
  }
  if (project.definition_of_done) push("dod", `Done when: ${project.definition_of_done}`);
  if (project.target_audience) push("audience", `For: ${project.target_audience}`);

  const all = (directives?.directives ?? []).filter((d) => d.kind === "directive");
  const active: ProjectDirective[] = all
    .filter((d) => Boolean(d.active) && d.retired_at == null && d.scope === "project")
    .sort((a, b) => a.created_at - b.created_at);
  const newestAt = active.length > 0 ? active[active.length - 1].created_at : null;
  for (const d of active) {
    lines.push({
      key: `directive:${d.id}`,
      text: clip(d.body),
      isNew: d.created_at === newestAt && d === active[active.length - 1],
      author: d.author_user_id,
      at: d.created_at,
    });
  }
  return { lines, version: 1 + all.length };
}

/** "You" for the signed-in person, else the id as given. */
export function personLabel(userId: string | null, callerUserId: string): string {
  if (!userId) return "Someone";
  return userId === callerUserId ? "You" : userId;
}

/** One letter for an avatar bubble. */
export function initial(name: string): string {
  const ch = name.trim()[0];
  return ch ? ch.toUpperCase() : "?";
}
