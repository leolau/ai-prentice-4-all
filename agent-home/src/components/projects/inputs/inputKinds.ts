import type { MemoryRow, ProjectLink, ProjectLinkKind } from "@/types";

/**
 * Pure rules for project inputs: what a file's role means as a link kind,
 * how a typed link-or-note becomes a pointer, and how a project's links
 * group into the Inputs tab's three sections. No React, no fetch.
 */

/** What an attached file is *for*. */
export type FileRole = "template" | "reference";

export const FILE_ROLES: { value: FileRole; label: string; explain: string }[] = [
  {
    value: "reference",
    label: "Reference to read",
    explain: "Background the agent reads before it works.",
  },
  {
    value: "template",
    label: "Template to match",
    explain: "An example of what good looks like — the agent copies its shape.",
  },
];

export const DEFAULT_FILE_ROLE: FileRole = "reference";

/** The link kinds an upload may be registered under. */
export type UploadLinkKind = "file" | "sample" | "reference";

/** `template` → `sample` (the run's "Samples to match"), `reference` → `reference`. */
export function roleToLinkKind(role: FileRole): "sample" | "reference" {
  return role === "template" ? "sample" : "reference";
}

/** The role a link kind stands for; a plain `file` link has none. */
export function linkKindToRole(kind: ProjectLinkKind): FileRole | null {
  if (kind === "sample") return "template";
  if (kind === "reference") return "reference";
  return null;
}

export const NOTE_PREFIX = "note:";

export function isNoteRef(ref: string): boolean {
  return ref.startsWith(NOTE_PREFIX);
}

const URL_RE = /^https?:\/\/\S+$/i;
const BARE_DOMAIN_RE = /^(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/?#]\S*)?$/i;

export function isUrlRef(ref: string): boolean {
  return URL_RE.test(ref);
}

/** A link or note the user typed, ready to POST to `/links`. */
export interface LinkEntry {
  kind: "url" | "reference";
  ref: string;
  label: string;
}

export const NOTE_MAX_CHARS = 2000;
const LABEL_MAX_CHARS = 120;

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

/**
 * Typed text → a pointer. Anything that looks like a web address is a `url`
 * link; everything else is a note, stored as a `reference` link whose ref
 * carries the note itself (there is no separate notes store).
 */
export function parseLinkOrNote(raw: string): LinkEntry | null {
  const text = raw.trim();
  if (!text) return null;
  if (URL_RE.test(text)) {
    return { kind: "url", ref: text, label: clip(text, LABEL_MAX_CHARS) };
  }
  if (BARE_DOMAIN_RE.test(text)) {
    const ref = `https://${text}`;
    return { kind: "url", ref, label: clip(text, LABEL_MAX_CHARS) };
  }
  const note = text.slice(0, NOTE_MAX_CHARS);
  return {
    kind: "reference",
    ref: `${NOTE_PREFIX}${note}`,
    label: clip(note.replace(/\s+/g, " "), LABEL_MAX_CHARS),
  };
}

/** A short title for a memory row: document, then topic, then its text. */
export function memoryTitle(row: Pick<MemoryRow, "text" | "topic" | "document_title">): string {
  const base = row.document_title || row.topic || row.text || "Memory";
  return clip(base.replace(/\s+/g, " ").trim(), LABEL_MAX_CHARS);
}

export function memoryLink(row: Pick<MemoryRow, "id" | "text" | "topic" | "document_title">): {
  kind: "memory";
  ref: string;
  label: string;
} {
  return { kind: "memory", ref: row.id, label: memoryTitle(row) };
}

/**
 * What to search memory for before the user types anything: the goal, or
 * failing that the first sentence of the description.
 */
export function suggestionQuery(goal: string, description = ""): string {
  const fromGoal = goal.trim();
  const fromDescription = description.trim().split(/(?<=[.!?。])\s+|\n/)[0]?.trim() ?? "";
  const q = fromGoal || fromDescription;
  if (q.length < 3) return "";
  return q.slice(0, 200);
}

/** The kinds the Inputs tab shows. `goal` is the project's own aim, not an input. */
const LINK_SECTION_KINDS: ProjectLinkKind[] = ["url", "todo", "arrival", "conversation"];

export interface InputGroups {
  files: ProjectLink[];
  memories: ProjectLink[];
  links: ProjectLink[];
}

function newestFirst(a: ProjectLink, b: ProjectLink): number {
  return (b.added_at ?? 0) - (a.added_at ?? 0);
}

/**
 * Split a project's links into the three Inputs sections:
 * - **files** — uploads and pointers to stored files, whatever their role
 *   (`file`, `sample`, `reference` that is neither a note nor a URL);
 * - **memories** — `memory` links;
 * - **links** — URLs, notes, to-dos, arrivals and conversations.
 */
export function groupInputLinks(
  links: Partial<Record<ProjectLinkKind, ProjectLink[]>>,
): InputGroups {
  const files: ProjectLink[] = [...(links.file ?? [])];
  const memories: ProjectLink[] = [...(links.memory ?? [])];
  const other: ProjectLink[] = [];
  for (const link of [...(links.sample ?? []), ...(links.reference ?? [])]) {
    if (isNoteRef(link.ref) || isUrlRef(link.ref)) other.push(link);
    else files.push(link);
  }
  for (const kind of LINK_SECTION_KINDS) other.push(...(links[kind] ?? []));
  return {
    files: files.sort(newestFirst),
    memories: memories.sort(newestFirst),
    links: other.sort(newestFirst),
  };
}

export function inputCount(groups: InputGroups): number {
  return groups.files.length + groups.memories.length + groups.links.length;
}

/** The user-facing role of any input link. */
export function inputRoleLabel(link: Pick<ProjectLink, "kind" | "ref">): string {
  switch (link.kind) {
    case "sample":
      return isUrlRef(link.ref) ? "Link · template to match" : "Template to match";
    case "reference":
      if (isNoteRef(link.ref)) return "Note";
      return isUrlRef(link.ref) ? "Link · reference" : "Reference to read";
    case "file":
      return "File";
    case "memory":
      return "Memory";
    case "url":
      return "Link";
    case "todo":
      return "To-do";
    case "arrival":
      return "Inbox item";
    case "conversation":
      return "Conversation";
    case "goal":
      return "Goal";
    default:
      return "Input";
  }
}

/** "you" for the caller, otherwise the id; `null` when nobody is recorded. */
export function addedByLabel(addedBy: string | null | undefined, callerUserId?: string): string | null {
  if (!addedBy) return null;
  return callerUserId && addedBy === callerUserId ? "you" : addedBy;
}

/** The note text a `note:` ref carries. */
export function noteText(ref: string): string {
  return isNoteRef(ref) ? ref.slice(NOTE_PREFIX.length) : ref;
}

/** A stable identity for a picked file — the same file dropped twice is one entry. */
export function fileIdentity(file: Pick<File, "name" | "size" | "lastModified">): string {
  return `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
}

/** The files from `incoming` not already in `known` (by identity), first occurrence wins. */
export function newFilesOnly<F extends Pick<File, "name" | "size" | "lastModified">>(
  known: Iterable<string>,
  incoming: Iterable<F>,
): F[] {
  const seen = new Set(known);
  const out: F[] = [];
  for (const file of incoming) {
    const id = fileIdentity(file);
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(file);
  }
  return out;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "PDF", "DOCX" — the badge on a file row. */
export function fileBadge(name: string): string {
  const ext = name.includes(".") ? name.split(".").pop() ?? "" : "";
  return ext ? ext.slice(0, 4).toUpperCase() : "FILE";
}

/** Same pointer? Links are keyed by (kind, profile, ref) on the server. */
export function sameLink(
  a: Pick<ProjectLink, "kind" | "profile" | "ref">,
  b: Pick<ProjectLink, "kind" | "profile" | "ref">,
): boolean {
  return a.kind === b.kind && a.profile === b.profile && a.ref === b.ref;
}

/**
 * A full `ProjectLink` from what a write answered (the BFF passes the stored
 * row through; fill anything it left out so the row can render at once).
 */
export function toProjectLink(
  partial: Pick<ProjectLink, "kind" | "ref"> & Partial<ProjectLink>,
  fallback: { projectId: string; profile: string; addedBy?: string | null; now?: number },
): ProjectLink {
  return {
    project_id: partial.project_id ?? fallback.projectId,
    kind: partial.kind,
    profile: partial.profile ?? fallback.profile,
    ref: partial.ref,
    label: partial.label ?? null,
    added_by: partial.added_by ?? fallback.addedBy ?? null,
    added_at: partial.added_at ?? fallback.now ?? Math.floor(Date.now() / 1000),
    resolved: partial.resolved ?? true,
  };
}

/** Put `link` first, replacing any row that is the same pointer. */
export function upsertLink(links: ProjectLink[], link: ProjectLink): ProjectLink[] {
  return [link, ...links.filter((l) => !sameLink(l, link))];
}

export function withoutLink(
  links: ProjectLink[],
  link: Pick<ProjectLink, "kind" | "profile" | "ref">,
): ProjectLink[] {
  return links.filter((l) => !sameLink(l, link));
}
