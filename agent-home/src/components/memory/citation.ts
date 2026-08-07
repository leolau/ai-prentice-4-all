/**
 * Where a memory came from, in words — the citation line under a card and in
 * the map popup.
 *
 * Two origins exist and they read differently: a memory written mid-chat
 * carries only a session id, while a RAG chunk carries the document it was
 * cut from. `detail` is the exact locator (a file path, a session id) and is
 * shown small, because "Pricing 2026 › Discounts" is what a person reads and
 * `/opt/data/.../pricing.md` is what they act on.
 *
 * Returns `null` when nothing was recorded, so the caller renders no line at
 * all rather than the phrase "unknown source", which reads like an error.
 */
export interface SourceCitation {
  label: string;
  detail: string | null;
}

export function describeSource(row: {
  kind?: string;
  source_session?: string | null;
  document_title?: string | null;
  section?: string | null;
  source_kind?: string | null;
  source_ref?: string | null;
}): SourceCitation | null {
  const title = row.document_title || null;
  const ref = row.source_ref || null;
  if (title || ref) {
    const name = title || ref || "";
    const label = row.section ? `${name} \u203a ${row.section}` : name;
    return {
      label: `${sourceKindWord(row.source_kind)}: ${label}`,
      detail: ref && ref !== title ? ref : null,
    };
  }
  if (row.source_session) {
    return { label: "From a chat", detail: `session ${row.source_session}` };
  }
  return null;
}

/**
 * Where to go for the full source, when somewhere exists.
 *
 * A chat memory links to its conversation; a Drive document links out to
 * Drive; an ingested local file links to the rest of its chunks on this page,
 * because agent-home deliberately serves no arbitrary path off the box.
 */
export interface SourceLink {
  href: string;
  text: string;
  external: boolean;
}

export function sourceLink(row: {
  source_session?: string | null;
  document_id?: string | null;
  source_kind?: string | null;
  source_ref?: string | null;
}): SourceLink | null {
  const ref = row.source_ref || "";
  if (/^https?:\/\//i.test(ref)) {
    return { href: ref, text: "Open the document", external: true };
  }
  if (row.document_id) {
    return {
      href: `/memory?document=${encodeURIComponent(row.document_id)}`,
      text: "See this document's passages",
      external: false,
    };
  }
  if (row.source_session) {
    return {
      href: `/chat?session=${encodeURIComponent(row.source_session)}`,
      text: "Open the conversation",
      external: false,
    };
  }
  return null;
}

/** "file" → "File", "drive" → "Google Drive", anything else title-cased. */
function sourceKindWord(kind: string | null | undefined): string {
  if (!kind) return "Document";
  if (kind === "drive") return "Google Drive";
  if (kind === "local" || kind === "file") return "File";
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}
